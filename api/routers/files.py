"""
Flat-file endpoints: analyze (guest) and download (signed in).

    POST /api/files/analyze    GUEST — upload 1+ files, get back:
                                 * how the files relate (categorization)
                                 * a cleaning report per group
                                 * a preview of the cleaned data (first rows)
                                 * a sample-size snapshot
                                 * the runtime-based price to download, and per table an
                                   estimate of the wait (CSV / Excel) from rows × fields
    POST /api/files/download   SIGNED IN — upload the same files again and
                               receive the cleaned file (CSV or Excel). Excel adds
                               a missing-value report sheet and, when rows were
                               removed for a blank ID, a "rows_missing_id" sheet.

WHY ARE THE FILES UPLOADED TWICE?
---------------------------------
So that we never have to STORE client data. The browser keeps the files the
user picked in memory (not on our server). When they sign up and click
"Download", the browser sends the same files again; the server re-runs the
exact same pipeline and streams the result straight back. Re-running is
cheap for files under the upload limit, and it means there is nothing on our
side to secure, expire, or delete later.
See docs/ARCHITECTURE.md -> "Stateless processing" for the trade-offs.
"""

from __future__ import annotations

import io
import re
from typing import Annotated, Literal

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from api.core.config import get_settings
from api.core.security import get_current_account
from api.services import account_store
from api.services.cleaning import dataframe_to_records
from api.services.pipeline import load_uploads, parse_column_actions, parse_column_names, run_pipeline
from api.services.pricing import download_estimate, runtime_cost

router = APIRouter(prefix="/files")

Strategy = Literal["keep", "drop_any"]


@router.post("/analyze")
async def analyze_files(
    files: Annotated[list[UploadFile], File(description="One or more flat files")],
    missing_strategy: Annotated[Strategy, Form()] = "keep",
    # Per-column choices, JSON: {"<group_id>": {"<column>": "keep|drop|mean|median|mode|auto|drop_rows"}}
    column_actions: Annotated[str, Form()] = "{}",
    # The client's own column names, JSON: {"<group_id>": {"<column key>": "new name"}}
    column_names: Annotated[str, Form()] = "{}",
):
    settings = get_settings()
    datasets = await load_uploads(files)
    result = run_pipeline(datasets, missing_strategy, parse_column_actions(column_actions),
                          parse_column_names(column_names))

    groups_out = []
    for g in result.groups:
        total_rows = len(g.cleaned)
        limit = settings.preview_max_rows
        groups_out.append(
            {
                "group_id": g.group_id,
                "label": g.label,
                "report": g.report.to_dict(),
                "sample_size": g.sample_size,
                # Wait + charge for downloading this table, shown in the
                # confirmation before the download starts.
                "download_estimate": download_estimate(
                    result.total_runtime_seconds, total_rows, len(g.cleaned.columns)
                ),
                "preview": {
                    "columns": [str(c) for c in g.cleaned.columns],
                    "rows": dataframe_to_records(g.cleaned, limit=limit),
                    "total_rows": total_rows,
                    # True only for very large files; the download has every row.
                    "truncated": total_rows > limit,
                },
            }
        )

    return {
        "categorization": result.categorization_dict(),
        "groups": groups_out,
        "runtime": runtime_cost(result.total_runtime_seconds),
    }


@router.post("/download")
async def download_cleaned(
    account: Annotated[dict, Depends(get_current_account)],
    files: Annotated[list[UploadFile], File()],
    group_id: Annotated[int, Form()] = 0,
    missing_strategy: Annotated[Strategy, Form()] = "keep",
    file_format: Annotated[Literal["csv", "xlsx"], Form()] = "csv",
    column_actions: Annotated[str, Form()] = "{}",
    # The amount the client saw and confirmed (from download_estimate). The
    # charge never goes above it, even if this run takes a little longer.
    confirmed_amount_usd: Annotated[float | None, Form(ge=0)] = None,
    column_names: Annotated[str, Form()] = "{}",
):
    datasets = await load_uploads(files)
    result = run_pipeline(datasets, missing_strategy, parse_column_actions(column_actions),
                          parse_column_names(column_names))

    group = next((g for g in result.groups if g.group_id == group_id), None)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That dataset group was not found.")

    # Record the billable runtime (metadata only — the data is not stored).
    cost = runtime_cost(result.total_runtime_seconds)
    if confirmed_amount_usd is not None:
        # Safeguard: never charge more than the total the client confirmed.
        cost["amount_usd"] = min(cost["amount_usd"], round(confirmed_amount_usd, 2))
    await account_store.record_usage(
        account_id=account["sub"],
        dataset_name=group.label,
        rows=len(group.cleaned),
        runtime_seconds=cost["runtime_seconds"],
        billable_minutes=cost["billable_minutes"],
        amount_usd=cost["amount_usd"],
    )

    # Build the file in memory and stream it back.
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", group.label).strip("_").lower() or "data"
    buffer: io.BytesIO
    if file_format == "xlsx":
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            group.cleaned.to_excel(writer, index=False, sheet_name="cleaned_data")
            pd.DataFrame(group.report.to_dict()["column_reports"]).to_excel(
                writer, index=False, sheet_name="missing_value_report"
            )
            # Rows removed because an ID was blank, so the client can look up
            # the right IDs, fill them in, and upload again.
            missing_ids = group.report.rows_missing_id
            if missing_ids is not None and len(missing_ids):
                missing_ids.to_excel(writer, index=False, sheet_name="rows_missing_id")
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        # utf-8-sig adds a byte-order mark so Excel opens accented characters correctly.
        buffer = io.BytesIO(group.cleaned.to_csv(index=False).encode("utf-8-sig"))
        media = "text/csv"
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{base}_cleaned.{file_format}"',
            # Lets the frontend show what the download cost.
            "X-Runtime-Seconds": str(cost["runtime_seconds"]),
            "X-Amount-USD": f"{cost['amount_usd']:.2f}",
        },
    )
