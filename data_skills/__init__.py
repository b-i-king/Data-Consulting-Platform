"""
data_skills — the platform's data skills, as documents AND as code.

Each folder holds one skill:
    SKILL.md   the procedure in plain English (Claude Code reads these too,
               through the .claude/skills shortcut that points here)
    skill.py   the same procedure as runnable pandas code

Run them from Python:
    from data_skills.registry import run_skill
    result = run_skill("trend-analysis", df, {"value_column": "amount"})

...from the command line:  python -m data_skills list
...or from the web API:    POST /api/skills/<skill>/run
"""
