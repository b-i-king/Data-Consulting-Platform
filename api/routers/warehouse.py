"""
Data Engineering: store cleaned data in fact.account_data — preview first.

    POST /api/warehouse/preview   upload files + choices -> how the data will be
                                  stored (each column's role, each metric's
                                  description), WHY, the first rows, and the
                                  round-trip check. Stores nothing.
    POST /api/warehouse/load      the same upload + the preview's fingerprint +
                                  consent -> load it. Refused unless the data and
                                  choices still match what was previewed.
    GET  /api/warehouse/summary   what this client has stored, recent loads, consent
    GET  /api/warehouse/export    a stored source as its original table (CSV) — the workspace opens it
                                  like an upload ("Use my stored data"), so the client
                                  and their consultant see the same data in every
                                  service; also "download my stored data"
    POST /api/warehouse/delete    "delete my stored data": everything in the
                                  fact.account_data for this client, and consent withdrawn

Signed in, through `get_acting_context`, so a consultant can do this for an
assigned client. PREVIEW and LOAD need the Data Engineering service (consultants
always have it). SUMMARY, EXPORT and DELETE don't: stored data belongs to the
client, so anyone on the account can see it, and the plan holder can delete it.

WHY "PREVIEW, THEN ACCEPT"?
---------------------------
Every file is stored in one shared shape (IDs, dates and categories kept as they
are; every number turned into a described metric row). The client sees exactly
how their data will be stored and why — and the proof that rebuilding it gives
back every cell — and decides. Nothing is stored until they accept AND consent to storage.
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
from api.services import account_store, audit, combine, dedupe, entitlements, hipaa, teams, warehouse_store, xero_guard
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
    active = await entitlements.active_services(ctx["account"], preview_all=ctx.get("admin", False))
    if SERVICE not in active:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="Loading into a warehouse is part of Data Engineering. Add it to your package first.")


async def _plan(files: list[UploadFile], group_id: int, missing_strategy: str, column_actions: str,
                column_names: str, transforms: str, choices: str, event_type: str,
                combine_spec: str = "{}", source_name: str | None = None) -> LoadPlan:
    """
    Re-run the cleaning pipeline on the upload and build the load plan for one table.

    Args:
        files, group_id, missing_strategy, column_actions, column_names, transforms:
            exactly as for /api/files/analyze, so the plan uses the data the client previewed.
        choices: str, JSON {"metric_columns", "id_columns", "metric_map"} — the client's
            changes to the guessed column roles and metric descriptions (all optional).
        event_type: str, an event type id ("transaction/income") or "" to guess.
        combine_spec: str, JSON — stack or join other tables of the upload with
            this one first (api/services/combine.py); "{}" = this table alone.
        source_name: str | None, store under this name instead (the source
            being replaced by "Update my most recent upload").

    Returns:
        LoadPlan: the plan (see api/services/warehouse_loader.py).

    Raises:
        HTTPException: 400 for bad JSON or an unknown column / event type; 404 when
            the table isn't in the upload; plus the upload errors of load_uploads.
    """
    try:
        chosen = json.loads(choices or "{}")
        spec = json.loads(combine_spec or "{}")
        if not isinstance(chosen, dict) or not isinstance(spec, dict):
            raise ValueError
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="'choices' and 'combine' must be JSON objects.")
    datasets = await load_uploads(files)
    result = run_pipeline(datasets, missing_strategy, parse_column_actions(column_actions),
                          parse_column_names(column_names), parse_transforms(transforms))
    group = next((g for g in result.groups if g.group_id == group_id), None)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That table was not found in the upload.")
    # The source's name in fact.account_data: the file's own name ("sales.csv")
    # when the table came from one file; the table's label when several files
    # were combined (e.g. one file per year).
    def name_of(gid: int, label: str) -> str:
        members = next((g.members for g in result.categorization.groups if g.group_id == gid), [])
        return members[0] if len(members) == 1 else label

    table, name = group.cleaned, name_of(group_id, group.label)
    try:
        if spec.get("mode") and spec.get("groups"):
            others = []
            for gid in spec["groups"]:
                other = next((g for g in result.groups if g.group_id == int(gid) and g.group_id != group_id), None)
                if other is None:
                    raise ValueError("A table to combine with was not found in the upload.")
                others.append((name_of(other.group_id, other.label), other.cleaned))
            table, name = combine.combine(table, name, others, spec)
        return build_plan(table, source_name or name, chosen, event_type or None)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))


async def _replace_target(holder: dict, replace_source: str) -> str | None:
    """
    Check "Update my most recent upload": the name must be this client's latest
    stored FILE upload (connector data is edited in the connected app instead).

    Returns:
        str | None: the source name to replace, or None when not replacing.

    Raises:
        HTTPException: 400 when it isn't the latest stored file upload.
    """
    if not replace_source:
        return None
    latest = (await warehouse_store.summary(holder)).get("latest_upload")
    if replace_source != latest:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="Only your most recent uploaded file can be updated here. Connected apps "
                                   "(QuickBooks, ...) are edited in that app, then refreshed.")
    return replace_source


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
    choices: Annotated[str, Form()] = "{}",
    event_type: Annotated[str, Form()] = "",
    combine_spec: Annotated[str, Form(alias="combine")] = "{}",
    replace_source: Annotated[str, Form()] = "",
):
    """
    POST /api/warehouse/preview — how the data will be transformed, why, and how it will look.

    Stores nothing. Send the reply's `fingerprint` back to /load to accept it.

    Args:
        ctx: dict, the acting context (injected by `get_acting_context`).
        files, group_id, missing_strategy, column_actions, column_names, transforms:
            as for /api/files/analyze.
        choices: str, JSON {"metric_columns", "id_columns", "metric_map"} to change the guesses.
        event_type: str, the event type, or "" to guess.
        combine_spec: str (form field "combine"), JSON — stack or join other tables first
            (api/services/combine.py), e.g. {"mode": "join", "groups": [1], "left_on": "customer_id"}.
        replace_source: str, "Update my most recent upload": the stored source to replace.

    Returns:
        dict: the LoadPlan (steps with "why", column plan, preview rows,
        round-trip check, counts, fingerprint, ready/problem) plus "storage":
        {"mode", "consented", "terms_version"} and "duplicates": {"records",
        "of", "record_ids", "sources"} — records already stored from ANOTHER
        source (api/services/dedupe.py), which the load can skip; and "changes" —
        when a file with this name is already stored (the same spreadsheet with
        rows or columns added): {"new_rows", "changed_rows", "unchanged_rows",
        "rows_not_in_file", "new_columns", "removed_columns"}
        (warehouse_store.changes_since_stored), else null.

    Raises:
        HTTPException: 403 without Data Engineering; 400/404 as in `_plan`.

    Example:
        POST /api/warehouse/preview   (multipart)  files=@sales.csv
        -> 200 {"event_type": "transaction/income", "steps": [{"title": ..., "why": ...}],
                "tables": {"columns": {...}, "account_data": {...}}, "fingerprint": "9f2c...", ...}
    """
    await _require_service(ctx)
    holder = await plan_holder(ctx["account"])
    hipaa.require_storage_allowed(holder)   # a HIPAA request waiting for an admin: nothing is stored yet
    target = await _replace_target(holder, replace_source)
    plan = await _plan(files, group_id, missing_strategy, column_actions, column_names, transforms,
                       choices, event_type, combine_spec, target)
    duplicates = await dedupe.find_duplicates(str(holder["id"]), plan.rows, plan.source_system, plan.source_name)
    # The same file uploaded again (rows or columns added): what's new since the stored copy.
    changes = None if target else await warehouse_store.changes_since_stored(holder, plan)
    return {**plan.to_dict(), "storage": await _storage_status(holder), "replacing": target, "duplicates": duplicates,
            "changes": changes}


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
    choices: Annotated[str, Form()] = "{}",
    event_type: Annotated[str, Form()] = "",
    combine_spec: Annotated[str, Form(alias="combine")] = "{}",
    replace_source: Annotated[str, Form()] = "",
    skip_duplicates: Annotated[bool, Form()] = False,
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
        choices, event_type, combine_spec, replace_source: exactly as sent to /preview.
        With replace_source, that source's stored rows are deleted and replaced
        by this upload (in one transaction).
        skip_duplicates: bool, "load only the new records": records the preview
        found already stored from another source are left out.

    Returns:
        dict: {"load_run": {...}, "result": {"rows_loaded", "rows_updated",
        "rows_already_stored"}, "summary": stored-data summary}.

    Raises:
        HTTPException: 403 without Data Engineering; 503 without a database (or
            warehouse tables); 400 without consent or for a plan with a problem;
            409 when the data or choices changed since the preview.

    Example:
        POST /api/warehouse/load   (multipart)  files=@sales.csv fingerprint=9f2c... consent=true
        -> 200 {"result": {"rows_loaded": 54630, ...}, "load_run": {"status": "succeeded", ...}, ...}
    """
    await _require_service(ctx)
    teams.require_editor(ctx)   # a view-only member can't store data
    if database.pool is None and not _memory_ok():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Connect a database first (DATABASE_URL) — there is nowhere to store the data yet.")
    holder = await plan_holder(ctx["account"])
    hipaa.require_storage_allowed(holder)   # a HIPAA request waiting for an admin: nothing is stored yet
    holder_id = str(holder["id"])
    if await account_store.active_storage_consent(holder_id) is None:
        if not consent:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail="Please tick the box to agree to storing this data before loading it.")
        await account_store.add_storage_consent(holder_id, TERMS_VERSION, str(ctx["account"]["id"]) if not ctx["acting"]
                                                else str(ctx["consultant"]["account_id"]))
    target = await _replace_target(holder, replace_source)
    plan = await _plan(files, group_id, missing_strategy, column_actions, column_names, transforms,
                       choices, event_type, combine_spec, target)
    if plan.problem:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=plan.problem)
    if plan.fingerprint != fingerprint:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="The data or the choices changed since the preview. Please preview again and re-check it.")
    run = await account_store.save_load_run({
        "account_id": holder_id, "source": plan.source_system, "source_name": plan.source_name,
        "event_type": plan.event_type, "rows_in": plan.counts["rows_in"], "column_plan": plan.column_plan,
        "plan_fingerprint": plan.fingerprint, "status": "running",
        "started_by": str(ctx["consultant"]["account_id"]) if ctx["acting"] else str(ctx["account"]["id"]),
    })
    skip = None
    if skip_duplicates:
        skip = set((await dedupe.find_duplicates(holder_id, plan.rows, plan.source_system, plan.source_name))["record_ids"])
    try:
        result = await warehouse_store.load(holder, plan,
                                            consultant_id=str(ctx["consultant"]["id"]) if ctx["acting"] else None,
                                            replace=target is not None, skip_records=skip)
    except Exception as exc:
        await account_store.save_load_run({"id": run["id"], "status": "failed", "error": str(exc)[:500],
                                           "finished_at": datetime.now(timezone.utc)})
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"The load failed and nothing was stored: {exc}")
    run = await account_store.save_load_run({
        "id": run["id"], "status": "succeeded", "rows_loaded": result["rows_loaded"],
        "rows_updated": result["rows_updated"], "rows_skipped": result["rows_already_stored"],
        "finished_at": datetime.now(timezone.utc),
    })
    await audit.record(ctx, "load", {"source": run.get("source_name"), "rows": result["rows_loaded"],
                                     "updated": result["rows_updated"]})   # healthcare clients only
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
            "rows_in": run["rows_in"], "rows_loaded": run["rows_loaded"], "rows_updated": run.get("rows_updated", 0),
            "rows_skipped": run["rows_skipped"],
            "status": run["status"], "error": run.get("error"), "started_at": iso(run["started_at"]),
            "finished_at": iso(run.get("finished_at"))}


@router.get("/summary")
async def warehouse_summary(ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    GET /api/warehouse/summary — what this client has stored, recent loads, consent.

    Args:
        ctx: dict, the acting context.

    Returns:
        dict: {"warehouse": what's stored (warehouse_store.summary), "loads": [load run, ...],
        "storage": {"mode", "consented", "terms_version"}}.

    Example:
        GET /api/warehouse/summary
        -> 200 {"warehouse": {"rows": 54630, "sources": [{"source_name": "sales.csv", ...}]}, "loads": [...], ...}
    """
    holder = await plan_holder(ctx["account"])
    await audit.record(ctx, "view_stored", {})   # healthcare clients only
    return {"warehouse": await warehouse_store.summary(holder),
            "loads": [_run_out(r) for r in await account_store.list_load_runs(str(holder["id"]))],
            "storage": await _storage_status(holder)}


@router.get("/export")
async def export_stored(ctx: Annotated[dict, Depends(get_acting_context)], source: str | None = None):
    """
    GET /api/warehouse/export — a stored source as its original table, as a CSV file.

    The workspace turns this file into the same kind of "picked file" as an
    upload, so dashboards, analysis, ML and AI all work on stored data with no
    extra code — for the client AND for their consultant, side by side.

    Args:
        ctx: dict, the acting context.
        source: str | None, one source by name (e.g. "sales.csv"); all of them when
            omitted (stacked, with a "source" column first).

    Returns:
        StreamingResponse: text/csv (UTF-8 with BOM, so Excel shows accents),
        named "<client>_<source>_stored.csv".

    Raises:
        HTTPException: 404 when nothing (from that source) is stored.

    Example:
        GET /api/warehouse/export?source=sales.csv
        -> 200 text/csv  order_id,order_date,customer_id,...
    """
    holder = await plan_holder(ctx["account"])
    df = await warehouse_store.export(holder, source or None)
    if df.empty:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Nothing is stored for this account yet.")
    name = holder.get("company_name") or f"{holder['first_name']}_{holder['last_name']}"
    kind = re.sub(r"\.[A-Za-z0-9]+$", "", source) if source else "all"      # "sales.csv" -> "sales"
    filename = re.sub(r"[^A-Za-z0-9_-]+", "_", f"{name}_{kind}_stored").strip("_").lower() + ".csv"
    body = df.to_csv(index=False).encode("utf-8-sig")
    stored = (await warehouse_store.summary(holder))["sources"]
    if any(s["source_system"] == "xero" and (not source or s["source_name"] == source) for s in stored):
        xero_guard.remember(body)   # includes Xero records: AI and ML are off for this file
    await audit.record(ctx, "export", {"source": source or "all", "rows": len(df)})   # healthcare clients only
    return StreamingResponse(io.BytesIO(body), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/delete")
async def delete_stored(body: DeleteStoredIn, ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    POST /api/warehouse/delete — "delete my stored data".

    Deletes everything stored in fact.account_data for this client right away and
    withdraws their storage consent (loading again needs a new consent). Only
    the plan holder (an individual, or a business's team owner) — or their
    consultant acting for them — may do it, and the request must say
    {"confirm": "DELETE"} so it can't happen by accident.

    Args:
        body: DeleteStoredIn, {"confirm": "DELETE"}.
        ctx: dict, the acting context.

    Returns:
        dict: {"deleted": {"rows", "sources"}, "consents_withdrawn": n,
        "summary": the (now empty) stored-data summary}.

    Raises:
        HTTPException: 400 without the confirmation word; 403 for a team member
            who isn't the owner.

    Example:
        POST /api/warehouse/delete  {"confirm": "DELETE"}
        -> 200 {"deleted": {"rows": 54630, "sources": 1}, ...}
    """
    if body.confirm != "DELETE":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail='Type DELETE to confirm.')
    holder = await plan_holder(ctx["account"])
    if str(holder["id"]) != str(ctx["account"]["id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only your team's owner can delete the stored data.")
    deleted = await warehouse_store.delete(holder)
    withdrawn = await account_store.withdraw_storage_consent(str(holder["id"]))
    await audit.record(ctx, "delete_stored", {"rows": deleted.get("rows") if isinstance(deleted, dict) else None})
    return {"deleted": deleted, "consents_withdrawn": withdrawn, "summary": await warehouse_store.summary(holder)}
