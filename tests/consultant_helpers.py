"""
Test helper: put someone through consultant screening (invite + application).

Consultants are no longer requested at sign-up (api/services/consultants.py):
an admin invites the email, the person applies from their signed-in account,
and an admin decides. Tests that need an approved consultant call
`apply_as_consultant`, then approve as before.
"""

# A complete, valid screening application (api/models/accounts.py ConsultantApplicationIn).
APPLICATION = {
    "services": ["data_cleaning", "data_visualization"],
    "experience": "3_5",
    "tools": "SQL, pandas, Power BI",
    "linkedin_url": "https://www.linkedin.com/in/example",
    "work_sample_url": "https://github.com/example/sales-dashboard",
    "project_summary": ("Built a weekly sales dashboard for a regional bakery chain: cleaned three years of "
                        "point-of-sale exports, matched products across stores, and showed margin by item."),
    "agree_conduct": True,
}


def apply_as_consultant(client, admin_headers: dict, applicant_token: str, email: str, **answers):
    """
    Invite `email` (as the admin), then send the application as that person.

    Args:
        client: the module's FastAPI TestClient.
        admin_headers: dict, the admin's Authorization header.
        applicant_token: str, the applicant's access token.
        email: str, the applicant's email (the one invited).
        **answers: fields that replace APPLICATION's.

    Returns:
        httpx.Response: the POST /api/accounts/consultant-request reply.

    Example:
        >>> apply_as_consultant(client, _h(admin), kim_token, "kim@example.com").json()["consultant_request"]
        'pending'
    """
    res = client.post("/api/admin/consultant-invites", headers=admin_headers, json={"email": email})
    assert res.status_code == 200, res.text
    return client.post("/api/accounts/consultant-request", headers={"Authorization": f"Bearer {applicant_token}"},
                       json={**APPLICATION, **answers})
