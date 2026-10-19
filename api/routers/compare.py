"""
"Compare over time" (beta) endpoint.

    POST /api/compare/over-time   two open tables, added up by week or month and
                                  lined up on the same periods (api/services/compare.py)

Takes the same multipart upload and cleaning choices as every analysis, plus
`spec` (JSON): {"period": "month",
                "left":  {"group_id": 0, "date": "played_at", "value": "hours", "agg": "sum"},
                "right": {"group_id": 1, "date": "invoice_date", "value": "line_amount", "agg": "sum"}}.
Part of the Dashboard page, so it needs sign-in and Data Visualization in the
package (403 otherwise; skill_access.require). Nothing is stored.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from api.core.security import get_acting_context
from api.services import compare as compare_service, skill_access
from api.services.pipeline import load_uploads, parse_column_actions, parse_column_names, parse_transforms, run_pipeline
from data_skills._core import SkillInputError

router = APIRouter(prefix="/compare")


@router.post("/over-time")
async def over_time(
    ctx: Annotated[dict, Depends(get_acting_context)],
    files: Annotated[list[UploadFile], File()],
    spec: Annotated[str, Form()],
    missing_strategy: Annotated[Literal["keep", "drop_any"], Form()] = "keep",
    column_actions: Annotated[str, Form()] = "{}",
    column_names: Annotated[str, Form()] = "{}",
    transforms: Annotated[str, Form()] = "{}",
):
    """
    POST /api/compare/over-time — two tables, by week or month, side by side.

    Returns:
        dict: {"periods", "left": {"label", "values"}, "right": {...}, "overlap",
        "correlation", "summary", "cautions", "period"}.

    Raises:
        HTTPException: 401 without sign-in; 403 without Data Visualization in the
            package; 400 for a bad spec, an unknown table or column, or no dates / numbers.

    Example:
        POST /api/compare/over-time  files=@games.csv files=@invoices.csv spec={...}
        -> 200 {"periods": ["2026-01-01", ...], "correlation": -0.42, "summary": "Across 9 months ...", ...}
    """
    await skill_access.require(ctx, "data_visualization")
    try:
        s = json.loads(spec)
        period, sides = s.get("period", "month"), [s["left"], s["right"]]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail='spec must look like {"period": "month", "left": {...}, "right": {...}}.')
    datasets = await load_uploads(files)
    result = run_pipeline(datasets, missing_strategy, parse_column_actions(column_actions),
                          parse_column_names(column_names), parse_transforms(transforms))
    groups = {g.group_id: g for g in result.groups}
    try:
        built = []
        for side in sides:
            group = groups.get(int(side.get("group_id", -1)))
            if group is None:
                raise SkillInputError("That table isn't open.")
            agg = side.get("agg", "sum")
            built.append((compare_service.series(group.cleaned, side.get("date", ""), side.get("value"), agg, period),
                          f"{'rows' if agg == 'count' else side.get('value')} ({group.label})"))
        return compare_service.compare(built[0][0], built[1][0], built[0][1], built[1][1], period)
    except SkillInputError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
