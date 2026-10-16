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
from api.core.security import get_acting_context
from api.services import account_store, entitlements, fair_use
from api.services.cleaning import dataframe_to_records
from api.services.pipeline import (
    load_uploads,
    parse_column_actions,
    parse_column_names,
    parse_transforms,
    run_pipeline,
)
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
    # Transform steps, JSON: {"<group_id>": [{"op": "change_type", ...}, ...]} (api/services/transforms.py)
    transforms: Annotated[str, Form()] = "{}",
):
    """
    POST /api/files/analyze — GUEST: upload 1+ files and see how they would be cleaned.

    No sign-in. Files are read in memory (never saved), grouped into related
    tables, cleaned, and summarized. For each group the reply has the cleaning
    report, a sample-size snapshot, the wait/charge estimate to download it, and
    a short preview (the first PREVIEW_MAX_ROWS rows, like `df.head()`). The
    browser calls this again each time the user changes a choice.

    Args:
        files: list[UploadFile], one or more flat files (multipart form field "files").
        missing_strategy: str, "keep" (default) or "drop_any" — the default
            missing-value handling.
        column_actions: str, JSON {"<group_id>": {"<column>": "keep|drop|mean|median|
            mode|auto|drop_rows"}}; default "{}".
        column_names: str, JSON {"<group_id>": {"<column key>": "new name"}}; default "{}".
        transforms: str, JSON {"<group_id>": [{"op": "change_type", ...}, ...]}
            (see api/services/transforms.py); default "{}".

    Returns:
        dict: {"categorization": how the files relate,
        "groups": [{"group_id", "label", "report", "sample_size",
        "download_estimate", "preview": {"columns", "rows", "total_rows",
        "truncated"}}, ...],
        "runtime": {"runtime_seconds", "billing_increment_seconds",
        "billable_minutes", "rate_per_minute_usd", "amount_usd"}}.

    Raises:
        HTTPException: 400 for no files, too many files, duplicate names,
            unreadable files or malformed JSON choices; 413 for a file over
            MAX_UPLOAD_MB.

    Example:
        POST /api/files/analyze   (multipart/form-data)
        files=@sales.csv  missing_strategy=keep
        -> 200 {"categorization": {...},
                "groups": [{"group_id": 0, "label": "sales",
                            "preview": {"columns": ["date", "amount"], "rows": [...],
                                        "total_rows": 1200, "truncated": true}, ...}],
                "runtime": {"runtime_seconds": 0.21, "amount_usd": 0.5, ...}}
    """
    settings = get_settings()
    datasets = await load_uploads(files)
    result = run_pipeline(datasets, missing_strategy, parse_column_actions(column_actions),
                          parse_column_names(column_names), parse_transforms(transforms))

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
    # Whose download this is: the signed-in client, or the client a consultant
    # is working on (X-Acting-As) — then it's billed to that client.
    ctx: Annotated[dict, Depends(get_acting_context)],
    files: Annotated[list[UploadFile], File()],
    group_id: Annotated[int, Form()] = 0,
    missing_strategy: Annotated[Strategy, Form()] = "keep",
    file_format: Annotated[Literal["csv", "xlsx"], Form()] = "csv",
    column_actions: Annotated[str, Form()] = "{}",
    # The amount the client saw and confirmed (from download_estimate). The
    # charge never goes above it, even if this run takes a little longer.
    confirmed_amount_usd: Annotated[float | None, Form(ge=0)] = None,
    column_names: Annotated[str, Form()] = "{}",
    transforms: Annotated[str, Form()] = "{}",
):
    """
    POST /api/files/download — SIGNED IN: re-upload the same files and get one cleaned table back.

    Re-runs exactly the same pipeline as /analyze (so nothing is ever stored),
    picks one group, records the billable runtime (metadata only) and streams
    the file. Charge rules: the per-minute runtime price, capped at the amount
    the client confirmed, $0 when their plan includes downloads, and $0 for a
    repeat of the exact same data downloaded in the fair-use window. Over the
    fair-use limit (DAILY_DOWNLOAD_LIMIT per DOWNLOAD_WINDOW_HOURS) -> 429. A
    consultant acting for a client (X-Acting-As) bills the client and is
    recorded on the usage row.

    Args:
        ctx: dict, the acting context (injected by `get_acting_context`).
        files: list[UploadFile], the same files sent to /analyze.
        group_id: int, which cleaned table to download (default 0).
        missing_strategy: str, "keep" (default) or "drop_any".
        file_format: str, "csv" (default, UTF-8 with BOM so Excel shows accents)
            or "xlsx" (adds a "missing_value_report" sheet and, if rows were
            removed for a blank ID, a "rows_missing_id" sheet).
        column_actions: str, JSON per-column missing-value choices (as for /analyze).
        confirmed_amount_usd: float | None, the price the client saw and
            confirmed; the charge never goes above it.
        column_names: str, JSON column renames (as for /analyze).
        transforms: str, JSON transform steps (as for /analyze).

    Returns:
        StreamingResponse: the file as an attachment named "<label>_cleaned.<csv|xlsx>",
        with headers X-Runtime-Seconds, X-Amount-USD (what it cost) and
        X-Repeat-Download ("1" when it was a free repeat).

    Raises:
        HTTPException: 401/403/404 from `get_acting_context`; 404 "That dataset
            group was not found."; 400/413 for upload or choice problems;
            429 when the account is over its fair-use download limit.

    Example:
        POST /api/files/download   (multipart/form-data)
        Authorization: Bearer eyJ...
        files=@sales.csv  group_id=0  file_format=csv  confirmed_amount_usd=0.50
        -> 200 text/csv
           Content-Disposition: attachment; filename="sales_cleaned.csv"
           X-Amount-USD: 0.50
    """
    datasets = await load_uploads(files)
    result = run_pipeline(datasets, missing_strategy, parse_column_actions(column_actions),
                          parse_column_names(column_names), parse_transforms(transforms))

    group = next((g for g in result.groups if g.group_id == group_id), None)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That dataset group was not found.")

    # Fair use: at most DAILY_DOWNLOAD_LIMIT downloads per rolling window, and
    # the exact same result downloaded again in that window isn't charged twice.
    # Only a one-way fingerprint of the table is kept — never the data.
    decision = await fair_use.evaluate_download(str(ctx["account"]["id"]), group.cleaned)
    if not decision.allowed:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail=decision.message)

    # Record the billable runtime (metadata only — the data is not stored).
    cost = runtime_cost(result.total_runtime_seconds)
    if confirmed_amount_usd is not None:
        # Safeguard: never charge more than the total the client confirmed.
        cost["amount_usd"] = min(cost["amount_usd"], round(confirmed_amount_usd, 2))
    if ctx.get("admin") or await entitlements.downloads_included(ctx["account"]):
        cost["amount_usd"] = 0.0   # an admin, or their plan includes Data Cleaning: no per-minute charge
    if decision.repeat:
        cost["amount_usd"] = 0.0   # same data already downloaded (and paid for) recently
    await account_store.record_usage(
        account_id=str(ctx["account"]["id"]),
        consultant_id=str(ctx["consultant"]["id"]) if ctx["acting"] else None,
        dataset_name=group.label,
        rows=len(group.cleaned),
        runtime_seconds=cost["runtime_seconds"],
        billable_minutes=cost["billable_minutes"],
        amount_usd=cost["amount_usd"],
        data_fingerprint=decision.fingerprint,
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
            # "1" when this was a free repeat of data downloaded recently.
            "X-Repeat-Download": "1" if decision.repeat else "0",
        },
    )
