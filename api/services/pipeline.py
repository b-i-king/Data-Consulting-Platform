"""
The flat-file pipeline: upload -> read -> categorize -> combine -> clean.

WHAT THIS FILE DOES
-------------------
Glues the individual services together in the order a consultant would work:

    1. load_uploads()    read every uploaded file into DataFrames
                         (api/services/file_reader.py)
    2. categorize()      single file? or several — same structure, split by
                         year, or different kinds of data?
                         (api/services/categorizer.py)
    3. combine_group()   stack files that share a structure into one table
    4. clean_dataframe() run the user's transform steps, clean column names
                         (and apply the user's own names),
                         remove duplicates, measure missing values, and apply
                         the user's missing-value choices (drop / fill a
                         column, drop rows) — api/services/cleaning.py
    5. sample_size_snapshot()  "is there enough data?" (api/services/sample_size.py)

The routers call `run_pipeline()` for both the guest preview and the signed-in
download, so both always produce exactly the same cleaned data.

NOTHING IS SAVED: the uploaded bytes and DataFrames exist only for the
duration of the request and are released by Python afterwards.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass

import pandas as pd
from fastapi import HTTPException, UploadFile, status

from api.core.config import get_settings
from api.services.categorizer import CategorizationResult, categorize, combine_group
from api.services.cleaning import CleaningReport, MissingStrategy, clean_dataframe
from api.services.file_reader import FileReadError, LoadedDataset, UnsupportedFileError, read_file
from api.services.sample_size import sample_size_snapshot


@dataclass
class GroupResult:
    """The cleaned output for one group of files (or the single file)."""

    group_id: int
    label: str
    cleaned: pd.DataFrame
    report: CleaningReport
    sample_size: dict


@dataclass
class PipelineResult:
    categorization: CategorizationResult
    groups: list[GroupResult]
    total_runtime_seconds: float

    def categorization_dict(self) -> dict:
        return asdict(self.categorization)


async def load_uploads(files: list[UploadFile]) -> list[LoadedDataset]:
    """
    Read every uploaded file, enforcing the size and count limits from
    settings. Errors are converted to HTTP 400/413 responses with messages
    that are safe to show the user.
    """
    settings = get_settings()
    if not files:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Please choose at least one file.")
    if len(files) > settings.max_files_per_upload:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Please upload {settings.max_files_per_upload} files or fewer at a time.",
        )

    datasets: list[LoadedDataset] = []
    for upload in files:
        # Read one byte past the limit: if we get it, the file is too big.
        raw = await upload.read(settings.max_upload_bytes + 1)
        if len(raw) > settings.max_upload_bytes:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"'{upload.filename}' is larger than {settings.max_upload_mb} MB. "
                       "Book a consultation for larger datasets.",
            )
        try:
            datasets.extend(read_file(upload.filename or "upload", raw))
        except (UnsupportedFileError, FileReadError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
        finally:
            await upload.close()

    # Two files with the same name would collide in the categorizer.
    names = [d.name for d in datasets]
    if len(names) != len(set(names)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Two uploaded files have the same name.")
    return datasets


def _parse_by_group(text: str | None, field_name: str, example: str) -> dict[int, dict[str, str]]:
    """JSON like {"0": {"column": "value"}} -> {0: {"column": "value"}}, or a friendly 400."""
    try:
        raw = json.loads(text or "{}")
        if not isinstance(raw, dict) or not all(isinstance(v, dict) for v in raw.values()):
            raise ValueError
        return {int(k): {str(c): str(a) for c, a in v.items()} for k, v in raw.items()}
    except (ValueError, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must look like {example}.")


def parse_column_actions(text: str | None) -> dict[int, dict[str, str]]:
    """
    Read the `column_actions` form field: JSON like
        {"0": {"amount": "median", "notes": "drop"}}
    i.e. group id -> {column -> action}. The browser sends the same value to
    analyze, download and skills, so all three see identical data.
    """
    return _parse_by_group(text, "column_actions", '{"0": {"column name": "median"}}')


def parse_column_names(text: str | None) -> dict[int, dict[str, str]]:
    """
    Read the `column_names` form field: the client's own column names, JSON like
        {"0": {"unnamed_column_4": "store_code", "amount_2": "refund_amount"}}
    i.e. group id -> {column key -> new name}. Sent with the same requests as
    column_actions.
    """
    return _parse_by_group(text, "column_names", '{"0": {"amount_2": "refund_amount"}}')


def parse_transforms(text: str | None) -> dict[int, list[dict]]:
    """
    Read the `transforms` form field: the client's transform steps per table,
    JSON like {"0": [{"op": "promote_headers"}, {"op": "change_type",
    "column": "order_date", "to": "date"}]} (see api/services/transforms.py).
    """
    try:
        raw = json.loads(text or "{}")
        if not isinstance(raw, dict) or not all(isinstance(v, list) for v in raw.values()):
            raise ValueError
        return {int(k): v for k, v in raw.items()}
    except (ValueError, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail='transforms must look like {"0": [{"op": "promote_headers"}]}.')


def run_pipeline(
    datasets: list[LoadedDataset],
    missing_strategy: MissingStrategy = "keep",
    column_actions: dict[int, dict[str, str]] | None = None,
    column_names: dict[int, dict[str, str]] | None = None,
    transforms: dict[int, list[dict]] | None = None,
) -> PipelineResult:
    """Steps 2–5 of the pipeline. Timed so runtime billing is accurate."""
    started = time.perf_counter()
    categorization = categorize(datasets)

    groups: list[GroupResult] = []
    for group in categorization.groups:
        combined = combine_group(datasets, group.members)
        try:
            cleaned, report = clean_dataframe(
                combined, dataset_name=group.label, missing_strategy=missing_strategy,
                column_actions=(column_actions or {}).get(group.group_id),
                column_names=(column_names or {}).get(group.group_id),
                transforms=(transforms or {}).get(group.group_id),
            )
        except ValueError as exc:  # e.g. an unknown fill method, two columns given one name, a bad step
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
        groups.append(
            GroupResult(
                group_id=group.group_id,
                label=group.label,
                cleaned=cleaned,
                report=report,
                sample_size=sample_size_snapshot(cleaned),
            )
        )

    return PipelineResult(
        categorization=categorization,
        groups=groups,
        total_runtime_seconds=round(time.perf_counter() - started, 4),
    )
