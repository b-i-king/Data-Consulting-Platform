"""
pii-flag — find personal information before sharing (Data Cleaning).

Python implementation of SKILL.md in this folder (adapted from Daniel
Rosehill's skill, MIT). Each column is checked two ways:

  * by NAME   — first_name, email, phone, ssn, dob, address, ...
  * by VALUE  — regular expressions on a sample: emails, US phone numbers,
                Social Security numbers, credit-card numbers (checked with the
                Luhn algorithm so random 16-digit IDs don't count), IP
                addresses, ZIP codes, dates of birth

Risk levels: HIGH (SSN, card numbers, full DOB), MEDIUM (email, phone,
street address, full name), LOW (city, ZIP, IP). The report never prints the
personal values themselves — only masked examples.
"""

from __future__ import annotations

import re

import pandas as pd

from data_skills._core import SkillResult, SkillSpec

PATTERNS = {
    "email": (re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$"), "medium"),
    "phone": (re.compile(r"^(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}$"), "medium"),
    "ssn": (re.compile(r"^\d{3}-\d{2}-\d{4}$"), "high"),
    "credit_card": (re.compile(r"^(?:\d[ -]?){13,19}$"), "high"),
    "ip_address": (re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$"), "low"),
    "zip_code": (re.compile(r"^\d{5}(-\d{4})?$"), "low"),
}
NAME_HINTS = {
    "ssn": ("ssn", "social_security", "high"), "dob": ("dob", "birth", "high"),
    "first_name": ("first_name", "firstname", "medium"), "last_name": ("last_name", "lastname", "medium"),
    "full_name": ("full_name", "customer_name", "medium"), "email": ("email", "e_mail", "medium"),
    "phone": ("phone", "mobile", "medium"), "address": ("address", "street", "medium"),
    "city": ("city", "city", "low"), "zip_code": ("zip", "postal", "low"),
}
RANK = {"high": 3, "medium": 2, "low": 1}

SKILL = SkillSpec(
    id="pii-flag",
    title="Personal-information (PII) scan",
    description="Flag columns that hold names, emails, phones, SSNs, card numbers, and other personal data.",
)


def luhn_ok(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def mask(value: str) -> str:
    """'ana.diaz@example.com' -> 'a***@example.com'; '4045551234' -> '******1234'."""
    v = str(value)
    if "@" in v:
        user, _, domain = v.partition("@")
        return f"{user[:1]}***@{domain}"
    return "*" * max(len(v) - 4, 0) + v[-4:]


def run(df: pd.DataFrame) -> SkillResult:
    found = []
    for col in df.columns:
        norm = re.sub(r"[\s\-.]+", "_", str(col).strip().lower())
        hits: dict[str, str] = {}
        for kind, (h1, h2, risk) in NAME_HINTS.items():
            if h1 in norm or h2 in norm:
                hits[kind] = risk
        sample = df[col].dropna().astype(str).str.strip().head(500)
        match_share = {}
        if len(sample):
            for kind, (pattern, risk) in PATTERNS.items():
                matched = sample[sample.str.match(pattern)]
                if kind == "credit_card":
                    matched = matched[matched.map(luhn_ok)]
                share = len(matched) / len(sample)
                if share >= 0.5:
                    hits.setdefault(kind, risk)
                    match_share[kind] = share
        if hits:
            risk = max(hits.values(), key=RANK.get)
            example = next(iter(sample), "")
            found.append({"column": str(col), "pii_type": ", ".join(sorted(hits)), "risk": risk,
                          "detected_by": "name + values" if match_share and len(hits) else ("values" if match_share else "name"),
                          "masked_example": mask(example) if example else ""})

    found.sort(key=lambda r: RANK[r["risk"]], reverse=True)
    high = [r["column"] for r in found if r["risk"] == "high"]
    summary = (f"{len(found)} column(s) contain personal information"
               + (f", including HIGH-risk data in {', '.join(high)}." if high else ".") if found
               else "No personal information detected.")
    findings = [f"{r['column']}: {r['pii_type']} ({r['risk']} risk)" for r in found]
    if found:
        findings.append("Before sharing: drop or hash these columns, or keep only what the analysis needs.")
    return SkillResult(skill=SKILL.id, summary=summary, findings=findings, tables={"pii_columns": found})
