"""
Data Engineering: load cleaned data into the client's warehouse — preview first.

    POST /api/warehouse/preview   upload files + choices -> how the data will be
                                  TRANSFORMED to fit the warehouse, WHY, and a
                                  preview of each table. Stores nothing.
    POST /api/warehouse/load      the same upload + the preview's fingerprint +
                                  consent -> load it. Refused unless the data and
                                  choices still match what was previewed.
    GET  /api/warehouse/summary   what this client has stored, recent loads, consent
    GET  /api/warehouse/export    the stored data as a CSV — the workspace opens it
                                  like an upload ("Use my stored data"), so the client
                                  and their consultant see the same data in every
                                  service; also "download my stored data"
    POST /api/warehouse/delete    "delete my stored data": everything in the
                                  warehouse for this client, and consent withdrawn

Signed in, through `get_acting_context`, so a consultant can do this for an
assigned client. PREVIEW and LOAD need the Data Engineering service (consultants
always have it). SUMMARY, EXPORT and DELETE don't: stored data belongs to the
client, so anyone on the account can see it, and the plan holder can delete it.

WHY "PREVIEW, THEN ACCEPT"?
---------------------------
A spreadsheet doesn't fit the warehouse as-is (one sales file becomes customers,
products and events). The client sees exactly how their data will be changed and
why, and decides. Nothing is stored until they accept AND consent to storage.
Storing client data is the one opt-in exception to "stateless processing" —
docs/ARCHITECTURE.md -> "Managed storage (opt-in exception)".
"""

from __future__ import annotations

import io
import json
import re
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from api.core import database
from api.core.security import get_acting_context
from api.models.accounts import DeleteStoredIn
from api.services import account_store, entitlements, warehouse_store
from api.services.entitlements import plan_holder

from api.services.pipeline import load_uploads, parse_column_actions, parse_column_names, parse_transforms, run_pipeline
from api.services.warehouse_loader import LoadPlan, build_plan

router = APIRouter(prefix="/warehouse")

SERVICE = "data_engineering"
# The storage terms version saved with each consent (table storage_consents).
# The terms themselves are the web page web/app/storage-terms/page.tsx — when
# that page changes, bump this AND the VERSION on the page, together.
TERMS_VERSION = "2026-10-15"


async def _require_service(ctx: dict) -> None:
    """403 unless the account (or the consultant looking) has Data Engineering."""
    active = await entitlements.active_services(ctx["account"], preview_all=ctx["consultant"] is not None)
    if SERVICE not in active:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="Loading into a warehouse is part of Data Engineering. Add it to your package first.")


async def _plan(files: list[UploadFile], group_id: int, missing_strategy: str, column_actions: str,
                column_names: str, transforms: str, mapping: str, event_type: str) -> LoadPlan:
    """
    Re-run the cleaning pipeline on the upload and build the load plan for one table.

    Args:
        files, group_id, missing_strategy, column_actions, column_names, transforms:
            exactly as for /api/files/analyze, so the plan uses the data the client previewed.
        mapping: str, JSON {role: column or ""} — the client's changes to the guessed mapping.
        event_type: str, an event type id ("transaction/income") or "" to guess.

    Returns:
        LoadPlan: the plan (see api/services/warehouse_loader.py).

    Raises:
        HTTPException: 400 for bad JSON or an unknown column / event type; 404 when
            the table isn't in the upload; plus the upload errors of load_uploads.
    """
    try:
        chosen = json.loads(mapping or "{}")
        if not isinstance(chosen, dict):
            raise ValueError
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="'mapping' must be a JSON object.")
    datasets = await load_uploads(files)
    result = run_pipeline(datasets, missing_strategy, parse_column_actions(column_actions),
                          parse_column_names(column_names), parse_transforms(transforms))
    group = next((g for g in result.groups if g.group_id == group_id), None)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That table was not found in the upload.")
    try:
        return build_plan(group.cleaned, group.label, chosen, event_type or None)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))


async def _storage_status(holder: dict) -> dict:
    """
    Where a load would go, and whether this client has consented (shown next to the preview).

    mode: "database" (Supabase connected), "memory" (no DATABASE_URL: local test
    mode, forgotten on restart) or "unavailable" (DATABASE_URL set but unreachable).
    """
    consent = await account_store.active_storage_consent(str(holder["id"]))
    mode = "database" if database.pool is not None else ("memory" if _memory_ok() else "unavailable")
    return {"mode": mode, "consented": consent is not None, "terms_version": TERMS_VERSION}


@router.post("/preview")
async def preview_load(
    ctx: Annotated[dict, Depends(get_acting_context)],
    files: Annotated[list[UploadFile], File()],
    group_id: Annotated[int, Form()] = 0,
    missing_strategy: Annotated[str, Form()] = "keep",
    column_actions: Annotated[str, Form()] = "{}",
    column_names: Annotated[str, Form()] = "{}",
    transforms: Annotated[str, Form()] = "{}",
    mapping: Annotated[str, Form()] = "{}",
    event_type: Annotated[str, Form()] = "",
):
    """
    POST /api/warehouse/preview — how the data will be transformed, why, and how it will look.

    Stores nothing. Send the reply's `fingerprint` back to /load to accept it.

    Args:
        ctx: dict, the acting context (injected by `get_acting_context`).
        files, group_id, missing_strategy, column_actions, column_names, transforms:
            as for /api/files/analyze.
        mapping: str, JSON {role: column or ""} to change the guessed mapping.
        event_type: str, the event type, or "" to guess.

    Returns:
        dict: the LoadPlan (steps with "why", table previews, counts,
        fingerprint, ready/problem) plus "storage": {"database", "consented",
        "terms_version"}.

    Raises:
        HTTPException: 403 without Data Engineering; 400/404 as in `_plan`.

    Example:
        POST /api/warehouse/preview   (multipart)  files=@sales.csv
        -> 200 {"event_type": "transaction/income", "steps": [{"title": ..., "why": ...}],
                "tables": {"fact_event": {...}, "dim_customer": {...}}, "fingerprint": "9f2c...", ...}
    """
    await _require_service(ctx)
    plan = await _plan(files, group_id, missing_strategy, column_actions, column_names, transforms,
                       mapping, event_type)
    return {**plan.to_dict(), "storage": await _storage_status(await plan_holder(ctx["account"]))}


@router.post("/load")
async def accept_and_load(
    ctx: Annotated[dict, Depends(get_acting_context)],
    files: Annotated[list[UploadFile], File()],
    fingerprint: Annotated[str, Form(min_length=1, max_length=64)],
    consent: Annotated[bool, Form()] = False,
    group_id: Annotated[int, Form()] = 0,
    missing_strategy: Annotated[str, Form()] = "keep",
    column_actions: Annotated[str, Form()] = "{}",
    column_names: Annotated[str, Form()] = "{}",
    transforms: Annotated[str, Form()] = "{}",
    mapping: Annotated[str, Form()] = "{}",
    event_type: Annotated[str, Form()] = "",
):
    """
    POST /api/warehouse/load — the client ACCEPTED the preview: load it.

    Checks, in order: Data Engineering; a database is connected; storage consent
    (given now with consent=true, or before); the plan rebuilt from this upload
    has the SAME fingerprint as the accepted preview. Then loads and records a
    load_runs row (succeeded or failed).

    Args:
        ctx: dict, the acting context.
        files: the same upload as the preview.
        fingerprint: str, the preview's fingerprint (what was accepted).
        consent: bool, True when the person ticked "store my data" now.
        group_id, missing_strategy, column_actions, column_names, transforms,
        mapping, event_type: exactly as sent to /preview.

    Returns:
        dict: {"load_run": {...}, "result": {"rows_loaded", "rows_already_stored",
        "customers", "products"}, "summary": warehouse summary}.

    Raises:
        HTTPException: 403 without Data Engineering; 503 without a database (or
            warehouse tables); 400 without consent or for a plan with a problem;
            409 when the data or choices changed since the preview.

    Example:
        POST /api/warehouse/load   (multipart)  files=@sales.csv fingerprint=9f2c... consent=true
        -> 200 {"result": {"rows_loaded": 18210, ...}, "load_run": {"status": "succeeded", ...}, ...}
    """
    await _require_service(ctx)
    if database.pool is None and not _memory_ok():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Connect a database first (DATABASE_URL) — there is nowhere to store the data yet.")
    holder = await plan_holder(ctx["account"])
    holder_id = str(holder["id"])
    if await account_store.active_storage_consent(holder_id) is None:
        if not consent:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail="Please tick the box to agree to storing this data before loading it.")
        await account_store.add_storage_consent(holder_id, TERMS_VERSION, str(ctx["account"]["id"]) if not ctx["acting"]
                                                else str(ctx["consultant"]["account_id"]))
    plan = await _plan(files, group_id, missing_strategy, column_actions, column_names, transforms,
                       mapping, event_type)
    if plan.problem:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=plan.problem)
    if plan.fingerprint != fingerprint:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="The data or the choices changed since the preview. Please preview again and re-check it.")
    run = await account_store.save_load_run({
        "account_id": holder_id, "source_name": plan.source_name, "event_type": plan.event_type,
        "rows_in": plan.counts["rows_in"], "plan_fingerprint": plan.fingerprint, "status": "running",
        "started_by": str(ctx["consultant"]["account_id"]) if ctx["acting"] else str(ctx["account"]["id"]),
    })
    try:
        result = await warehouse_store.load(holder, plan)
    except Exception as exc:
        await account_store.save_load_run({"id": run["id"], "status": "failed", "error": str(exc)[:500],
                                           "finished_at": datetime.now(timezone.utc)})
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"The load failed and nothing was stored: {exc}")
    run = await account_store.save_load_run({
        "id": run["id"], "status": "succeeded", "rows_loaded": result["rows_loaded"],
        "rows_skipped": plan.counts["rows_skipped"] + result["rows_already_stored"],
        "finished_at": datetime.now(timezone.utc),
    })
    return {"load_run": _run_out(run), "result": result, "summary": await warehouse_store.summary(holder)}


def _memory_ok() -> bool:
    """
    Allow loading into the in-memory warehouse? Only when no DATABASE_URL is set at all
    (local development and tests), never when a database was configured but is down.
    """
    from api.core.config import get_settings

    return not get_settings().database_url


def _run_out(run: dict) -> dict:
    """One load_runs row for the browser (ids as text, times as ISO)."""
    iso = lambda v: v.isoformat() if isinstance(v, datetime) else v   # noqa: E731
    return {"id": str(run["id"]), "source_name": run["source_name"], "event_type": run["event_type"],
            "rows_in": run["rows_in"], "rows_loaded": run["rows_loaded"], "rows_skipped": run["rows_skipped"],
            "status": run["status"], "error": run.get("error"), "started_at": iso(run["started_at"]),
            "finished_at": iso(run.get("finished_at"))}


@router.get("/summary")
async def warehouse_summary(ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    GET /api/warehouse/summary — what this client has stored, recent loads, consent.

    Args:
        ctx: dict, the acting context.

    Returns:
        dict: {"warehouse": counts (warehouse_store.summary), "loads": [load run, ...],
        "storage": {"database", "consented", "terms_version"}}.

    Example:
        GET /api/warehouse/summary
        -> 200 {"warehouse": {"events": 18210, "customers": 400, ...}, "loads": [...], ...}
    """
    holder = await plan_holder(ctx["account"])
    return {"warehouse": await warehouse_store.summary(holder),
            "loads": [_run_out(r) for r in await account_store.list_load_runs(str(holder["id"]))],
            "storage": await _storage_status(holder)}


@router.get("/export")
async def export_stored(ctx: Annotated[dict, Depends(get_acting_context)], event_type: str | None = None):
    """
    GET /api/warehouse/export — the client's stored data as a CSV file.

    The workspace turns this file into the same kind of "picked file" as an
    upload, so dashboards, analysis, ML and AI all work on stored data with no
    extra code — for the client AND for their consultant, side by side.

    Args:
        ctx: dict, the acting context.
        event_type: str | None, only this type (e.g. "transaction/income"); all when omitted.

    Returns:
        StreamingResponse: text/csv (UTF-8 with BOM, so Excel shows accents),
        named "<client>_<type>_stored.csv".

    Raises:
        HTTPException: 404 when nothing (of that type) is stored.

    Example:
        GET /api/warehouse/export?event_type=transaction/income
        -> 200 text/csv  date,event_type,amount,quantity,...
    """
    holder = await plan_holder(ctx["account"])
    df = await warehouse_store.export(holder, event_type or None)
    if df.empty:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Nothing is stored for this account yet.")
    name = holder.get("company_name") or f"{holder['first_name']}_{holder['last_name']}"
    kind = (event_type or "all").replace("/", "_")
    filename = re.sub(r"[^A-Za-z0-9_-]+", "_", f"{name}_{kind}_stored").strip("_").lower() + ".csv"
    body = df.to_csv(index=False).encode("utf-8-sig")
    return StreamingResponse(io.BytesIO(body), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/delete")
async def delete_stored(body: DeleteStoredIn, ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    POST /api/warehouse/delete — "delete my stored data".

    Deletes everything stored in the warehouse for this client right away and
    withdraws their storage consent (loading again needs a new consent). Only
    the plan holder (an individual, or a business's team owner) — or their
    consultant acting for them — may do it, and the request must say
    {"confirm": "DELETE"} so it can't happen by accident.

    Args:
        body: DeleteStoredIn, {"confirm": "DELETE"}.
        ctx: dict, the acting context.

    Returns:
        dict: {"deleted": {"events", "customers", "products"}, "consents_withdrawn": n,
        "summary": the (now empty) warehouse summary}.

    Raises:
        HTTPException: 400 without the confirmation word; 403 for a team member
            who isn't the owner.

    Example:
        POST /api/warehouse/delete  {"confirm": "DELETE"}
        -> 200 {"deleted": {"events": 18210, "customers": 400, "products": 9}, ...}
    """
    if body.confirm != "DELETE":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail='Type DELETE to confirm.')
    holder = await plan_holder(ctx["account"])
    if str(holder["id"]) != str(ctx["account"]["id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only your team's owner can delete the stored data.")
    deleted = await warehouse_store.delete(holder)
    withdrawn = await account_store.withdraw_storage_consent(str(holder["id"]))
    return {"deleted": deleted, "consents_withdrawn": withdrawn, "summary": await warehouse_store.summary(holder)}
