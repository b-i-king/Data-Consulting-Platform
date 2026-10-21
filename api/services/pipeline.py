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
from api.services import xero_guard


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
        """
        The categorization result as plain dicts, ready to return as JSON.

        dataclasses.asdict converts the CategorizationResult and everything nested
        inside it (its groups and their members) into dicts and lists.

        Returns:
            dict: the categorization (kind of upload, groups, explanations) as a dict.

        Example:
            >>> result = run_pipeline(read_file("sales.csv", b"a,b" + bytes([10]) + b"1,2"))
            >>> result.categorization_dict()["groups"][0]["label"]
            'General data'
        """
        return asdict(self.categorization)


async def load_uploads(files: list[UploadFile]) -> list[LoadedDataset]:
    """
    Read every uploaded file into DataFrames (step 1 of the pipeline).

    Read every uploaded file, enforcing the size and count limits from
    settings. Errors are converted to HTTP 400/413 responses with messages
    that are safe to show the user.

    Rules:
    - at least one file, and no more than settings.max_files_per_upload
    - each file at most settings.max_upload_bytes (only one byte past the limit
      is read, so a huge file is refused without loading it all)
    - each upload is closed after reading, whether it succeeded or not
    - dataset names must be unique (e.g. two "sales.csv" files are refused)
    - tables with no rows are dropped (no cleaning or analysis is spent on
      them); if every table is empty the upload is refused with 422

    Args:
        files: the files from a FastAPI multipart form (UploadFile objects).

    Returns:
        list[LoadedDataset]: every table found, in upload order (one file can
        give several, e.g. Excel sheets or files inside a zip).

    Raises:
        HTTPException: 400 for no files, too many files, an unsupported or
            unreadable file, or duplicate names; 413 for a file over the size limit;
            422 when every table has no rows.

    Example:
        datasets = await load_uploads([UploadFile("sales.csv"), UploadFile("refunds.xlsx")])
        # -> [LoadedDataset(name="sales.csv", ...), LoadedDataset(name="refunds.xlsx", ...)]
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
            tables = read_file(upload.filename or "upload", raw)
            fp = xero_guard.fingerprint(raw)   # lets the AI / ML tools recognize data that came from Xero
            for t in tables:
                t.sha256 = fp
            datasets.extend(tables)
        except (UnsupportedFileError, FileReadError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
        finally:
            await upload.close()

    # A table with column names but no rows has nothing to clean or analyze:
    # drop it here, before any work (and memory) is spent on it.
    empty = [d.name for d in datasets if len(d.df) == 0]
    datasets = [d for d in datasets if len(d.df) > 0]
    if not datasets:
        raise HTTPException(422,
                            detail=f"{', '.join(empty)} {'has' if len(empty) == 1 else 'have'} column names "
                                   "but no rows, so there is nothing to analyze yet.")

    # Two files with the same name would collide in the categorizer.
    names = [d.name for d in datasets]
    if len(names) != len(set(names)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Two uploaded files have the same name.")
    return datasets


def _parse_by_group(text: str | None, field_name: str, example: str) -> dict[int, dict[str, str]]:
    """
    Parse a per-group JSON form field into {group id: {column: value}}.

    JSON like {"0": {"column": "value"}} -> {0: {"column": "value"}}, or a friendly 400.
    Group ids are converted to int (JSON keys are always text), and every
    column name and value is converted to str. An empty or missing field
    means "no choices" and gives {}.

    Args:
        text: the raw form field (JSON text), or None if it was not sent.
        field_name: the form field's name, used in the error message.
        example: a valid example value, shown in the error message.

    Returns:
        dict[int, dict[str, str]]: group id -> {column -> value}.

    Raises:
        HTTPException: 400 if the text is not JSON, not an object of objects,
            or has a group id that is not a whole number.

    Example:
        >>> _parse_by_group('{"0": {"amount": "median"}}', "column_actions", "...")
        {0: {'amount': 'median'}}
        >>> _parse_by_group(None, "column_actions", "...")
        {}
    """
    try:
        raw = json.loads(text or "{}")
        if not isinstance(raw, dict) or not all(isinstance(v, dict) for v in raw.values()):
            raise ValueError
        return {int(k): {str(c): str(a) for c, a in v.items()} for k, v in raw.items()}
    except (ValueError, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must look like {example}.")


def parse_column_actions(text: str | None) -> dict[int, dict[str, str]]:
    """
    Read the `column_actions` form field (the user's missing-value choices).

    Read the `column_actions` form field: JSON like
        {"0": {"amount": "median", "notes": "drop"}}
    i.e. group id -> {column -> action}. The browser sends the same value to
    analyze, download and skills, so all three see identical data.

    Args:
        text: the raw form field (JSON text), or None/"" for no choices.

    Returns:
        dict[int, dict[str, str]]: group id -> {column -> action}.

    Raises:
        HTTPException: 400 if the field is not shaped like the example above.

    Example:
        >>> parse_column_actions('{"0": {"amount": "median"}}')
        {0: {'amount': 'median'}}
    """
    return _parse_by_group(text, "column_actions", '{"0": {"column name": "median"}}')


def parse_column_names(text: str | None) -> dict[int, dict[str, str]]:
    """
    Read the `column_names` form field (the client's own column names).

    Read the `column_names` form field: the client's own column names, JSON like
        {"0": {"unnamed_column_4": "store_code", "amount_2": "refund_amount"}}
    i.e. group id -> {column key -> new name}. Sent with the same requests as
    column_actions.

    Args:
        text: the raw form field (JSON text), or None/"" for no renames.

    Returns:
        dict[int, dict[str, str]]: group id -> {column key -> new name}.

    Raises:
        HTTPException: 400 if the field is not shaped like the example above.

    Example:
        >>> parse_column_names('{"0": {"amount_2": "refund_amount"}}')
        {0: {'amount_2': 'refund_amount'}}
    """
    return _parse_by_group(text, "column_names", '{"0": {"amount_2": "refund_amount"}}')


def parse_transforms(text: str | None) -> dict[int, list[dict]]:
    """
    Read the `transforms` form field (the client's transform steps per table).

    Read the `transforms` form field: the client's transform steps per table,
    JSON like {"0": [{"op": "promote_headers"}, {"op": "change_type",
    "column": "order_date", "to": "date"}]} (see api/services/transforms.py).

    Only the outer shape is checked here (an object of lists, whole-number
    keys); each step is validated later by the transforms service.

    Args:
        text: the raw form field (JSON text), or None/"" for no steps.

    Returns:
        dict[int, list[dict]]: group id -> list of step dicts, in order.

    Raises:
        HTTPException: 400 if the field is not an object whose values are lists.

    Example:
        >>> parse_transforms('{"0": [{"op": "promote_headers"}]}')
        {0: [{'op': 'promote_headers'}]}
        >>> parse_transforms(None)
        {}
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
    """
    Steps 2–5 of the pipeline. Timed so runtime billing is accurate.

    For every group the categorizer finds:
    1. stack the group's files into one table (combine_group)
    2. clean it with the user's choices for that group id (transform steps,
       column names, missing-value actions) — clean_dataframe
    3. add the "is there enough data?" snapshot (sample_size_snapshot)

    Used by both the guest preview and the signed-in download, so they always
    produce the same cleaned data. Nothing is saved.

    Args:
        datasets: the tables from load_uploads().
        missing_strategy: what to do with missing values by default
            ("keep" leaves them as they are; see cleaning.MissingStrategy).
        column_actions: group id -> {column -> action}, from parse_column_actions().
        column_names: group id -> {column key -> new name}, from parse_column_names().
        transforms: group id -> list of transform steps, from parse_transforms().

    Returns:
        PipelineResult: the categorization, one GroupResult per group, and the
        total runtime in seconds (rounded to 4 decimals).

    Raises:
        HTTPException: 400 when cleaning rejects the user's choices (e.g. an
            unknown fill method, two columns given the same name, a bad step).

    Example:
        >>> datasets = read_file("sales.csv", b"a,b" + bytes([10]) + b"1,2" + bytes([10]) + b"3,4")
        >>> result = run_pipeline(datasets, column_actions={0: {"b": "median"}})
        >>> result.groups[0].cleaned.shape, result.groups[0].sample_size["rows"]
        ((2, 2), 2)
    """
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
