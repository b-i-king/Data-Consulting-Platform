"""
warehouses — the client's data warehouses and CRM, read where they live.

Medium and large companies keep their data in a warehouse (Snowflake, Google
BigQuery, AWS Redshift) or a CRM (Salesforce). We never copy it: we list their
tables, and either SUMMARIZE in their source (api/services/pushdown.py — only
the small answer comes back) or read a capped sample. Everything is read-only.

How each one connects (the platform calls their web APIs directly — no heavy
driver packages on the server):

  Snowflake   SQL API (https://<account>.snowflakecomputing.com/api/v2/statements),
              signed in with a KEY PAIR: the client creates a read-only user
              with an RSA public key and gives us the private key; we sign a
              short-lived token (JWT) for each call. No password is stored.
  BigQuery    REST API with a SERVICE ACCOUNT the client creates with only
              "BigQuery Data Viewer" + "BigQuery Job User". Every query has a
              cap on bytes scanned (BIGQUERY_MAX_BYTES) so a summary can never
              run up the client's bill; tables are sampled with the free
              tabledata.list, not a query.
  Redshift    Through its PostgreSQL-compatible endpoint: paste a connection
              string in the "PostgreSQL database" card (cloud_sources.py).
  Salesforce  "Connect Salesforce" sign-in (OAuth, api/services/oauth.py), then
              aggregate SOQL queries on one object (Opportunity, Account, …).

Credentials are saved ENCRYPTED in dim.data_connections (cloud_sources.encrypt),
like the PostgreSQL connection string.

Column kinds (for checking a summary request): "number", "date" or "text".
"""

from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import io
import json
import time

import jwt
from cryptography.hazmat.primitives import serialization
from fastapi import HTTPException, status

from api.core.config import get_settings
from api.services import cloud_sources, oauth, pushdown

SAMPLE_ROWS = 20_000            # rows when opening a whole table (like other live reads)
SALESFORCE_API = "v61.0"
SALESFORCE_SAMPLE = 2_000       # one page of records

NUMBER_TYPES = {"smallint", "integer", "bigint", "numeric", "decimal", "real", "double precision", "money", "number",
                "float", "float64", "int64", "int", "bignumeric", "double", "currency", "percent", "long"}
DATE_TYPES = {"date", "datetime", "timestamp", "timestamp without time zone", "timestamp with time zone",
              "timestamp_ntz", "timestamp_ltz", "timestamp_tz"}


def kind_of(data_type: str) -> str:
    """A source's column type -> "number", "date" or "text" ("NUMBER(38,2)" -> "number")."""
    t = (data_type or "").lower().split("(")[0].strip()
    return "number" if t in NUMBER_TYPES else "date" if t in DATE_TYPES else "text"


def _rows_csv(columns: list[str], rows: list[list]) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(columns)
    w.writerows(["" if v is None else v for v in r] for r in rows)
    return out.getvalue()


def _fail(source: str, res) -> HTTPException:
    """A plain-English error from a source's answer."""
    if res.status_code in (401, 403):
        return HTTPException(status.HTTP_400_BAD_REQUEST,
                             detail=f"{source} refused the sign-in or permission. Check the read-only user's access.")
    if res.status_code == 404:
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{source} couldn't find that table.")
    try:
        msg = (res.json().get("message") or res.json().get("error", {}).get("message") or "")[:200]
    except ValueError:
        msg = ""
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"{source} said: {msg or res.status_code}")


# ── Snowflake (SQL API + key-pair sign-in) ────────────────────────────────────

def snowflake_token(creds: dict) -> str:
    """
    A one-hour sign-in token (JWT) for the Snowflake SQL API, signed with the client's private key.

    Snowflake checks it against the PUBLIC key on the read-only user: the "iss"
    carries that key's fingerprint (SHA256 of the public key, base64).
    """
    try:
        key = serialization.load_pem_private_key(creds["private_key"].encode(), password=None)
    except (ValueError, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="That private key couldn't be read. Paste the whole PEM text (unencrypted).")
    public = key.public_key().public_bytes(serialization.Encoding.DER,
                                            serialization.PublicFormat.SubjectPublicKeyInfo)
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(public).digest()).decode()
    account = creds["account"].split(".")[0].upper()     # the account identifier, no region / cloud suffix
    user = creds["user"].upper()
    now = int(time.time())
    return jwt.encode({"iss": f"{account}.{user}.{fingerprint}", "sub": f"{account}.{user}", "iat": now,
                       "exp": now + 3600}, key, algorithm="RS256")


async def snowflake_run(creds: dict, sql: str) -> tuple[list[str], list[list]]:
    """Run one read-only statement through the SQL API; returns (column names, rows)."""
    host = f"https://{creds['account'].lower()}.snowflakecomputing.com"
    headers = {"Authorization": f"Bearer {snowflake_token(creds)}", "Accept": "application/json",
               "X-Snowflake-Authorization-Token-Type": "KEYPAIR_JWT"}
    body = {"statement": sql, "timeout": 60, **{k: creds[k].upper() for k in ("warehouse", "database", "schema", "role")
                                                if creds.get(k)}}
    async with oauth.http_client() as http:
        res = await http.post(f"{host}/api/v2/statements", json=body, headers=headers)
        for _ in range(10):                       # 202 = still running: ask again
            if res.status_code != 202:
                break
            await asyncio.sleep(1)
            res = await http.get(f"{host}{res.json()['statementStatusUrl']}", headers=headers)
    if res.status_code != 200:
        raise _fail("Snowflake", res)
    data = res.json()
    cols = [c["name"] for c in data.get("resultSetMetaData", {}).get("rowType", [])]
    return cols, data.get("data", [])


def _lit(text: str) -> str:
    """A text value inside SQL, single quotes doubled (catalog lookups only)."""
    return "'" + str(text).replace("'", "''") + "'"


async def snowflake_tables(creds: dict) -> list[dict]:
    _, rows = await snowflake_run(creds, "SELECT table_schema, table_name, row_count FROM information_schema.tables "
                                         "WHERE table_schema <> 'INFORMATION_SCHEMA' ORDER BY 1, 2 LIMIT 500")
    return [{"name": f"{s}.{t}", "rows": int(n) if n not in (None, "") else None} for s, t, n in rows]


async def snowflake_columns(creds: dict, table: str) -> dict[str, str]:
    schema, _, name = table.partition(".")
    _, rows = await snowflake_run(creds, "SELECT column_name, data_type FROM information_schema.columns WHERE "
                                         f"table_schema = {_lit(schema)} AND table_name = {_lit(name)} "
                                         "ORDER BY ordinal_position")
    return {c: kind_of(t) for c, t in rows}


# ── BigQuery (REST + service account) ─────────────────────────────────────────

BQ = "https://bigquery.googleapis.com/bigquery/v2"


async def bigquery_token(creds: dict) -> str:
    """Swap the service account's signed request for a one-hour access token."""
    sa = creds["service_account"]
    now = int(time.time())
    assertion = jwt.encode({"iss": sa["client_email"], "scope": "https://www.googleapis.com/auth/bigquery",
                            "aud": sa.get("token_uri", "https://oauth2.googleapis.com/token"), "iat": now,
                            "exp": now + 3600}, sa["private_key"], algorithm="RS256")
    async with oauth.http_client() as http:
        res = await http.post(sa.get("token_uri", "https://oauth2.googleapis.com/token"),
                              data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
    if res.status_code != 200:
        raise _fail("Google", res)
    return res.json()["access_token"]


async def _bq_get(creds: dict, path: str, params: dict | None = None) -> dict:
    token = await bigquery_token(creds)
    async with oauth.http_client() as http:
        res = await http.get(f"{BQ}{path}", params=params, headers={"Authorization": f"Bearer {token}"})
    if res.status_code != 200:
        raise _fail("BigQuery", res)
    return res.json()


async def bigquery_run(creds: dict, sql: str) -> tuple[list[str], list[list]]:
    """One query with a cap on bytes scanned (BIGQUERY_MAX_BYTES): over it, BigQuery refuses — nothing is billed."""
    token = await bigquery_token(creds)
    body = {"query": sql, "useLegacySql": False, "timeoutMs": 60_000,
            "maximumBytesBilled": str(get_settings().bigquery_max_bytes),
            **({"location": creds["location"]} if creds.get("location") else {})}
    async with oauth.http_client() as http:
        res = await http.post(f"{BQ}/projects/{creds['project']}/queries", json=body,
                              headers={"Authorization": f"Bearer {token}"})
    if res.status_code != 200:
        raise _fail("BigQuery", res)
    data = res.json()
    if not data.get("jobComplete", True):
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, detail="BigQuery is still working on that — try again.")
    cols = [f["name"] for f in data.get("schema", {}).get("fields", [])]
    return cols, [[cell.get("v") for cell in r["f"]] for r in data.get("rows", [])]


async def bigquery_tables(creds: dict) -> list[dict]:
    out = []
    for d in (await _bq_get(creds, f"/projects/{creds['project']}/datasets")).get("datasets", [])[:50]:
        ds = d["datasetReference"]["datasetId"]
        for t in (await _bq_get(creds, f"/projects/{creds['project']}/datasets/{ds}/tables")).get("tables", []):
            out.append({"name": f"{ds}.{t['tableReference']['tableId']}", "rows": None})
            if len(out) >= 500:
                return out
    return out


async def bigquery_columns(creds: dict, table: str) -> dict[str, str]:
    ds, _, name = table.partition(".")
    meta = await _bq_get(creds, f"/projects/{creds['project']}/datasets/{ds}/tables/{name}")
    return {f["name"]: kind_of(f["type"]) for f in meta.get("schema", {}).get("fields", [])}


async def bigquery_sample(creds: dict, table: str) -> tuple[list[str], list[list]]:
    """The first SAMPLE_ROWS rows with tabledata.list — FREE (no query, nothing scanned)."""
    ds, _, name = table.partition(".")
    meta = await _bq_get(creds, f"/projects/{creds['project']}/datasets/{ds}/tables/{name}")
    cols = [f["name"] for f in meta.get("schema", {}).get("fields", [])]
    data = await _bq_get(creds, f"/projects/{creds['project']}/datasets/{ds}/tables/{name}/data",
                         {"maxResults": SAMPLE_ROWS})
    return cols, [[cell.get("v") for cell in r["f"]] for r in data.get("rows", [])]


# ── Salesforce (REST + "Connect Salesforce" sign-in) ──────────────────────────

async def _sf_get(token: str, rest: str, path: str, params: dict | None = None) -> dict:
    async with oauth.http_client() as http:
        res = await http.get(f"{rest}{path}", params=params, headers={"Authorization": f"Bearer {token}"})
    if res.status_code != 200:
        raise _fail("Salesforce", res)
    return res.json()


async def salesforce_tables(token: str, rest: str) -> list[dict]:
    """The objects worth analyzing: queryable standard objects people use, and every custom one (…__c)."""
    common = {"Account", "Contact", "Lead", "Opportunity", "Case", "Campaign", "Order", "Product2", "Task", "Event",
              "Contract", "Quote", "OpportunityLineItem", "OrderItem", "Asset"}
    objs = (await _sf_get(token, rest, "sobjects/")).get("sobjects", [])
    return [{"name": o["name"], "label": o.get("label"), "rows": None} for o in objs
            if o.get("queryable") and (o["name"] in common or o.get("custom"))][:300]


async def salesforce_columns(token: str, rest: str, obj: str) -> dict[str, str]:
    fields = (await _sf_get(token, rest, f"sobjects/{obj}/describe")).get("fields", [])
    return {f["name"]: kind_of(f["type"]) for f in fields}


async def salesforce_run(token: str, rest: str, soql: str) -> tuple[list[str], list[list]]:
    recs = (await _sf_get(token, rest, "query", {"q": soql})).get("records", [])
    cols = [k for k in (recs[0] if recs else {}) if k != "attributes"]
    return cols, [[r.get(c) for c in cols] for r in recs]


# ── One door for the routers ──────────────────────────────────────────────────

async def tables(connector: str, src: dict) -> list[dict]:
    """The source's tables (or objects): [{"name", "rows"}]."""
    if connector == "snowflake":
        return await snowflake_tables(src["creds"])
    if connector == "bigquery":
        return await bigquery_tables(src["creds"])
    if connector == "salesforce":
        return await salesforce_tables(src["token"], src["rest"])
    raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown warehouse.")


async def columns(connector: str, src: dict, table: str) -> dict[str, str]:
    """A table's columns and their kinds — the table must be one the source lists (names are never trusted)."""
    if connector == "supabase":
        return await cloud_sources.table_columns(src, table)
    known = {t["name"] for t in await tables(connector, src)}
    if table not in known:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That table wasn't found.")
    if connector == "snowflake":
        return await snowflake_columns(src["creds"], table)
    if connector == "bigquery":
        return await bigquery_columns(src["creds"], table)
    return await salesforce_columns(src["token"], src["rest"], table)


async def summarize(connector: str, src: dict, table: str, raw: dict) -> dict:
    """
    Run a summary IN the source; only the answer comes back.

    Returns:
        dict: {"query" (the exact query that ran — shown to the client), "csv", "rows", "truncated": False,
        "name"}.
    """
    cols = await columns(connector, src, table)
    s = pushdown.validate(raw, table, cols)
    if connector == "salesforce":
        query = pushdown.build_soql(s)
        names, rows = await salesforce_run(src["token"], src["rest"], query)
    else:
        dialect = {"supabase": "postgres", "snowflake": "snowflake", "bigquery": "bigquery"}[connector]
        query = pushdown.build_sql(s, dialect)
        if connector == "supabase":
            names, rows = await cloud_sources.run_select(src, query)
        elif connector == "snowflake":
            names, rows = await snowflake_run(src["creds"], query)
        else:
            names, rows = await bigquery_run(src["creds"], query)
    label = f"{table.split('.')[-1]}_{s.agg}_{s.metric or 'rows'}" + (f"_by_{'_'.join(s.group_by)}" if s.group_by else "") \
        + (f"_per_{s.period}" if s.period else "")
    return {"query": query, "csv": _rows_csv(names, rows), "rows": len(rows), "truncated": False, "name": label}


async def sample(connector: str, src: dict, table: str) -> dict:
    """Open a whole table, capped (Snowflake: LIMIT; BigQuery: the free tabledata.list; Salesforce: one page)."""
    cols = await columns(connector, src, table)
    if connector == "snowflake":
        names, rows = await snowflake_run(src["creds"], f"SELECT * FROM {pushdown.table_ref(table, 'snowflake')} "
                                                        f"LIMIT {SAMPLE_ROWS}")
    elif connector == "bigquery":
        names, rows = await bigquery_sample(src["creds"], table)
    elif connector == "salesforce":
        fields = ", ".join(list(cols)[:100])          # SOQL has no SELECT *: the first 100 fields
        names, rows = await salesforce_run(src["token"], src["rest"], f"SELECT {fields} FROM {table} "
                                                                      f"LIMIT {SALESFORCE_SAMPLE}")
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown warehouse.")
    limit = SALESFORCE_SAMPLE if connector == "salesforce" else SAMPLE_ROWS
    return {"csv": _rows_csv(names, rows), "rows": len(rows), "truncated": len(rows) >= limit,
            "name": table.split(".")[-1]}


def pack_creds(creds: dict) -> str:
    """Warehouse credentials -> one encrypted text for dim.data_connections."""
    return cloud_sources.encrypt(json.dumps({"mode": "warehouse", "creds": creds}))


def unpack_creds(encrypted: str) -> dict:
    return json.loads(cloud_sources.decrypt(encrypted))["creds"]
