"""
data_skills — the platform's data skills, as documents AND as code.

Each folder holds one skill:
    README.md  the procedure in plain English (GitHub shows it when you open
               the folder; it starts with the skill's name and description)
    skill.py   the same procedure as runnable pandas code

Run them from Python:
    from data_skills.registry import run_skill
    result = run_skill("trend-analysis", df, {"value_column": "amount"})

...from the command line:  python -m data_skills list
...or from the web API:    POST /api/skills/<skill>/run
"""
