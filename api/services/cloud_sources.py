"""
Cloud data sources: a client's OWN database, read where it lives (never stored).

WHY THIS EXISTS
---------------
Clients whose data is already in a cloud database (Supabase or any
PostgreSQL) already have a system that manages it. They come for quick
analysis, dashboards, ML and AI — not for us to keep a copy. So this module:

  * checks and saves their database login (Settings -> Integrations ->
    "Supabase database"), ENCRYPTED;
  * lists their tables, schema by schema, and checks which ones have at
    least one row — empty tables are hidden and can't be opened, so no
    reading, cleaning or analysis is ever spent on them;
  * reads ONE table (up to MAX_ROWS rows) when they open it in the
    workspace, inside a READ ONLY transaction, and hands it back as a CSV
    file — the same way "Use my stored data" works. Nothing from it is stored
    on our side; Data Engineering's "Load into database" is switched off for
    cloud data, and Data Cleaning only REPORTS on it (it doesn't change it).
  * "Sign in with Supabase" (bottom of this file): no password at all —
    Supabase's Management API runs our SELECTs on its read-only endpoint.

KEEPING COSTS DOWN (memory = the server's monthly bill)
------------------------------------------------------
  * "Has rows?" uses PostgreSQL's own free row estimate first, and only asks
    the table itself (a one-row EXISTS check) when the estimate is 0 or unknown.
  * Rows are read in chunks of CHUNK_ROWS and turned into CSV chunk by chunk,
    so only one chunk is ever a DataFrame in memory.
  * Reading stops at MAX_ROWS rows or the upload size limit (MAX_UPLOAD_MB),
    whichever comes first — the workspace opens the CSV like an upload.
  * One connection per request, closed straight after.

SAFETY
------
  * Encryption: the login is encrypted with Fernet (AES + HMAC, from the
    `cryptography` package) using a key derived from JWT_SECRET_KEY. The
    plain text is never written anywhere.
  * Read-only: every query runs in a READ ONLY transaction with a statement
    timeout. We recommend a read-only database user anyway (docs/CONNECTORS.md).
  * Only real internet hosts: a host that resolves to a private, loopback or
    link-local address is refused (so nobody can make our server reach its
    own internal network). ALLOW_PRIVATE_DB_HOSTS=true switches that off for
    local development only.
  * Table names are only ever ones the database itself listed, quoted as
    identifiers — never pasted into SQL as typed.
"""

from __future__ import annotations

import base64
import hashlib
import io
import ipaddress
import json
import socket
from urllib.parse import unquote, urlparse

import asyncpg
import pandas as pd
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, status

from api.core.config import get_settings

MAX_ROWS = 100_000            # the most rows read from one table at a time
CONNECT_TIMEOUT_S = 10
STATEMENT_TIMEOUT_MS = 30_000
CHUNK_ROWS = 5_000            # rows turned into CSV at a time (keeps memory flat)
EXISTS_CHECKS_MAX = 300       # most "does it have rows?" checks per listing
EXISTS_TIMEOUT_MS = 2_000     # each check gives up after 2 seconds (a slow view -> "don't know")
# Schemas that belong to PostgreSQL or Supabase itself, not to the client's data.
SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "auth", "storage", "realtime", "supabase_functions",
                  "extensions", "graphql", "graphql_public", "pgsodium", "pgsodium_masks", "vault", "net", "cron",
                  "supabase_migrations", "pgbouncer", "_realtime", "_analytics", "dim", "fact")


def _fernet() -> Fernet:
    """The encryption key: 32 bytes derived from JWT_SECRET_KEY (changing that key makes saved logins unreadable)."""
    digest = hashlib.sha256(("cloud-sources:" + get_settings().jwt_secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(text: str) -> str:
    """Encrypt a database login for storage (dim.data_connections.credentials_encrypted)."""
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    """
    Decrypt a saved login.

    Raises:
        HTTPException: 409 when it can't be read (JWT_SECRET_KEY changed): connect again.
    """
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="The saved database login can't be read anymore. Please connect it again.")


def parse_dsn(dsn: str) -> dict:
    """
    Check a connection string and pull out what to show (never the password).

    Args:
        dsn: str, e.g. "postgresql://user:pass@db.abcd.supabase.co:5432/postgres".

    Returns:
        dict: {"host", "port", "database", "user", "label"}.

    Raises:
        HTTPException: 400 when it isn't a postgres:// or postgresql:// address with a host.

    Example:
        >>> parse_dsn("postgresql://ro:secret@db.abcd.supabase.co:5432/postgres")["label"]
        'db.abcd.supabase.co / postgres'
    """
    dsn = (dsn or "").strip()
    url = urlparse(dsn)
    if url.scheme not in ("postgres", "postgresql") or not url.hostname:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="Paste a PostgreSQL connection string, like postgresql://user:password@host:5432/postgres "
                                   "(Supabase: Project -> Connect -> Connection string).")
    database = unquote(url.path.lstrip("/")) or "postgres"
    return {"host": url.hostname, "port": url.port or 5432, "database": database,
            "user": unquote(url.username or ""), "label": f"{url.hostname} / {database}"}


def check_host(host: str) -> None:
    """
    Refuse hosts that point inside a private network (see SAFETY above).

    Raises:
        HTTPException: 400 when the host can't be found or is private / loopback / link-local.
    """
    if get_settings().allow_private_db_hosts:
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"We couldn't find the database host '{host}'.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail="That database address is on a private network. Use its public address.")


async def _connect(dsn: str) -> asyncpg.Connection:
    """Open a connection to the client's database (friendly errors)."""
    check_host(parse_dsn(dsn)["host"])
    try:
        return await asyncpg.connect(dsn, timeout=CONNECT_TIMEOUT_S, command_timeout=STATEMENT_TIMEOUT_MS / 1000,
                                     server_settings={"application_name": "data-consulting-platform (read-only)"})
    except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
        message = str(exc).split("\n")[0][:200] or exc.__class__.__name__
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"We couldn't connect to that database: {message}")


async def _has_rows(conn: asyncpg.Connection, schema: str, name: str) -> bool | None:
    """
    Does this table (or view) have at least one row?

    `SELECT EXISTS (SELECT 1 ... LIMIT 1)` stops at the first row it finds, so
    it costs almost nothing even on a huge table. It runs in a SAVEPOINT with a
    short timeout: a slow view gives None ("don't know") instead of failing
    the whole listing.

    Returns:
        bool | None: True / False, or None when the check timed out or was refused.
    """
    try:
        async with conn.transaction():   # a savepoint inside the read-only transaction
            await conn.execute(f"SET LOCAL statement_timeout = {EXISTS_TIMEOUT_MS}")
            return bool(await conn.fetchval(f"SELECT EXISTS (SELECT 1 FROM {_ident(schema)}.{_ident(name)} LIMIT 1)"))
    except (asyncpg.PostgresError, TimeoutError):
        return None


async def list_tables(dsn: str) -> list[dict]:
    """
    The client's tables and views (system schemas left out), each with
    whether it has any rows.

    Schemas with no tables never appear (there's nothing in them to list).
    For each table, PostgreSQL's own row estimate (pg_class.reltuples, kept up
    to date by autovacuum) answers "does it have rows?" for free. Only when
    the estimate is 0 or unknown (-1: never analyzed), and for views (which
    have no estimate), we ask the table itself with a one-row EXISTS check —
    at most EXISTS_CHECKS_MAX of them, so a database with thousands of tables
    stays quick.

    Args:
        dsn: str, the connection string (decrypted).

    Returns:
        list[dict]: [{"schema", "name", "kind" ("table" | "view"),
        "rows_estimate" (0 = unknown or empty), "has_rows" (True / False /
        None = couldn't tell)}], sorted by schema then name.

    Example:
        >>> await list_tables(dsn)
        [{'schema': 'public', 'name': 'orders', 'kind': 'table', 'rows_estimate': 18210, 'has_rows': True},
         {'schema': 'public', 'name': 'returns', 'kind': 'table', 'rows_estimate': 0, 'has_rows': False}]
    """
    conn = await _connect(dsn)
    try:
        async with conn.transaction(readonly=True):
            await conn.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
            rows = await conn.fetch(
                """SELECT t.table_schema AS schema, t.table_name AS name,
                          CASE t.table_type WHEN 'VIEW' THEN 'view' ELSE 'table' END AS kind,
                          coalesce(c.reltuples, -1)::bigint AS rows_estimate
                   FROM information_schema.tables t
                   LEFT JOIN pg_namespace n ON n.nspname = t.table_schema
                   LEFT JOIN pg_class c ON c.relnamespace = n.oid AND c.relname = t.table_name
                   WHERE t.table_schema <> ALL($1::text[]) AND t.table_schema NOT LIKE 'pg\\_%'
                   ORDER BY t.table_schema, t.table_name""", list(SYSTEM_SCHEMAS))
            tables, checks = [], 0
            for r in rows:
                estimate = int(r["rows_estimate"])
                if r["kind"] == "table" and estimate > 0:
                    has_rows = True                        # the free estimate already says yes
                elif checks < EXISTS_CHECKS_MAX:
                    checks += 1
                    has_rows = await _has_rows(conn, r["schema"], r["name"])
                else:
                    has_rows = None                        # too many to check: let them try opening it
                tables.append({**dict(r), "rows_estimate": max(estimate, 0), "has_rows": has_rows})
    finally:
        await conn.close()
    return tables


def summarize_schemas(tables: list[dict]) -> list[dict]:
    """
    One line per schema: how many tables, and how many have data.

    Args:
        tables: list[dict], from list_tables().

    Returns:
        list[dict]: [{"schema", "tables", "with_rows"}] in schema order. A
        table whose check timed out (has_rows None) counts as "may have rows".

    Example:
        >>> summarize_schemas([{"schema": "public", "has_rows": True}, {"schema": "public", "has_rows": False}])
        [{'schema': 'public', 'tables': 2, 'with_rows': 1}]
    """
    out: dict[str, dict] = {}
    for t in tables:
        s = out.setdefault(t["schema"], {"schema": t["schema"], "tables": 0, "with_rows": 0})
        s["tables"] += 1
        s["with_rows"] += t.get("has_rows") is not False
    return list(out.values())


def _ident(name: str) -> str:
    """Quote a SQL identifier ("Orders" -> "\\"Orders\\""), doubling any quote inside it."""
    return '"' + name.replace('"', '""') + '"'


def _cell_text(v):
    """One cell for CSV: JSON (dict/list) as JSON text, UUIDs and other objects as text."""
    if v is None or isinstance(v, (str, int, float)):
        return v
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str)
    return str(v)


def _csv_text_cells(df: pd.DataFrame) -> pd.DataFrame:
    """UUIDs, JSON (dict/list) and other objects become text, so they travel as CSV."""
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].map(_cell_text)
    return df


def csv_within(df: pd.DataFrame, max_bytes: int, header: bool = True) -> tuple[bytes, int, bool]:
    """
    A DataFrame as CSV bytes, cut to the rows that fit in max_bytes.

    CSV cells can contain line breaks, so rows are cut by count (estimated
    from bytes per row, then shrunk until it fits), never by splitting lines.

    Returns:
        tuple[bytes, int, bool]: (csv, rows written, True when rows were left out).

    Example:
        >>> csv_within(pd.DataFrame({"a": range(5)}), 10_000)[1:]
        (5, False)
    """
    df = _csv_text_cells(df)
    text = df.to_csv(index=False, header=header).encode("utf-8")
    if len(text) <= max_bytes or df.empty:
        return text, len(df), False
    fit = int(max_bytes / (len(text) / len(df)))
    while fit > 0:
        text = df.iloc[:fit].to_csv(index=False, header=header).encode("utf-8")
        if len(text) <= max_bytes:
            return text, fit, True
        fit = int(fit * 0.9)
    return b"", 0, True


async def read_table_csv(dsn: str, schema: str, name: str, limit: int = MAX_ROWS,
                         max_bytes: int | None = None) -> dict:
    """
    Read one table (or view) as CSV, read-only, using as little memory as possible.

    The steps, all on ONE connection in ONE read-only transaction:
      1. the table must be one of the client's (looked up in the catalog —
         the name is never trusted as typed);
      2. it must have at least one row (a one-row EXISTS check) — an empty
         table is refused BEFORE anything is read;
      3. rows come in chunks of CHUNK_ROWS through a server-side cursor; each
         chunk is turned into CSV and let go, so only one chunk is ever in
         memory as a DataFrame;
      4. reading stops at `limit` rows or `max_bytes` of CSV, whichever comes
         first. The byte cap defaults to the upload limit (MAX_UPLOAD_MB),
         because the workspace opens this CSV like an uploaded file.

    Args:
        dsn: str, the connection string (decrypted).
        schema: str, e.g. "public".
        name: str, e.g. "orders".
        limit: int, the most rows (1 to MAX_ROWS).
        max_bytes: int | None, the most CSV bytes (None = the upload limit).

    Returns:
        dict: {"csv": bytes, "rows": int, "columns": list[str], "truncated": bool}.

    Raises:
        HTTPException: 404 when it isn't one of the client's tables;
            422 when it has no rows (nothing to analyze).

    Example:
        >>> r = await read_table_csv(dsn, "public", "orders", 1000)
        >>> r["rows"], r["truncated"]
        (1000, True)
    """
    limit = max(1, min(int(limit), MAX_ROWS))
    if max_bytes is None:
        max_bytes = get_settings().max_upload_bytes - 64 * 1024   # a little room under the upload limit
    conn = await _connect(dsn)
    try:
        async with conn.transaction(readonly=True):
            await conn.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
            known = await conn.fetchval(
                """SELECT 1 FROM information_schema.tables
                   WHERE table_schema = $1 AND table_name = $2
                     AND table_schema <> ALL($3::text[]) AND table_schema NOT LIKE 'pg\\_%'""",
                schema, name, list(SYSTEM_SCHEMAS))
            if not known:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That table wasn't found in your database.")
            if await _has_rows(conn, schema, name) is False:
                raise HTTPException(422,
                                    detail=f"{schema}.{name} has no rows yet, so there is nothing to analyze.")
            # limit + 1: one extra row tells us whether the table has more than we read.
            stmt = await conn.prepare(f"SELECT * FROM {_ident(schema)}.{_ident(name)} LIMIT $1")
            columns = [a.name for a in stmt.get_attributes()]
            cursor = await stmt.cursor(limit + 1)
            out = io.BytesIO()
            rows, truncated = 0, False
            while True:
                records = await cursor.fetch(CHUNK_ROWS)
                if not records:
                    break
                if rows + len(records) > limit:            # the extra row: there is more
                    records, truncated = records[: limit - rows], True
                chunk = _csv_text_cells(pd.DataFrame([tuple(r) for r in records], columns=columns))
                text = chunk.to_csv(index=False, header=rows == 0).encode("utf-8")
                if out.tell() + len(text) > max_bytes:
                    # Keep only the rows that fit: estimate from this chunk's bytes per row,
                    # then shrink until it fits (CSV cells can hold line breaks, so lines can't be cut).
                    fit = int((max_bytes - out.tell()) / (len(text) / len(chunk)))
                    while fit > 0:
                        text = chunk.iloc[:fit].to_csv(index=False, header=rows == 0).encode("utf-8")
                        if out.tell() + len(text) <= max_bytes:
                            break
                        fit = int(fit * 0.9)
                    if fit > 0:
                        out.write(text)
                        rows += fit
                    truncated = True
                    break
                out.write(text)
                rows += len(chunk)
                if truncated:
                    break
    finally:
        await conn.close()
    if rows == 0:   # the EXISTS check couldn't tell and it was empty, or not even one row fit the byte cap
        raise HTTPException(422,
                            detail=f"{schema}.{name} has rows too large to open here. Book a consultation for it."
                            if truncated else f"{schema}.{name} has no rows yet, so there is nothing to analyze.")
    return {"csv": out.getvalue(), "rows": rows, "columns": columns, "truncated": truncated}


# ############################################################################
# "Sign in with Supabase" (OAuth): read through Supabase's Management API
# ############################################################################
# A client who signs in with Supabase (api/services/oauth.py) never gives us a
# database password. Instead, Supabase's Management API runs our SELECT
# queries for us as its built-in `supabase_read_only_user`
# (POST /v1/projects/{ref}/database/query/read-only — read-only by design).
# Same rules as above: empty tables hidden, read in chunks, capped, never stored.

MGMT_API = "https://api.supabase.com/v1"
MGMT_VIEW_MAX_ROWS = 20_000   # views can't be paged by row position, so one capped read


def _literal(text: str) -> str:
    """Quote a SQL text value ('O''Brien'), doubling any quote inside it."""
    return "'" + text.replace("'", "''") + "'"


async def _mgmt(token: str, method: str, path: str, body: dict | None = None):
    """Call the Supabase Management API with the client's access token; friendly errors."""
    from api.services import oauth

    async with oauth.http_client() as http:
        res = await http.request(method, f"{MGMT_API}{path}", json=body,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    if res.status_code == 401:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Supabase access was removed or expired. Please sign in again.")
    if res.status_code == 403:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="Supabase didn't allow that. The OAuth app needs 'Projects: read' and 'Database: read'.")
    if res.status_code == 429:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="Supabase asked us to slow down. Try again in a minute.")
    if res.status_code >= 400:
        message = ""
        try:
            message = str(res.json().get("message", ""))[:200]
        except (ValueError, AttributeError):
            pass
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Supabase couldn't run that: {message or res.status_code}")
    return res.json()


async def mgmt_query(token: str, ref: str, sql: str) -> list[dict]:
    """Run one read-only SELECT in the client's project; the rows as dicts."""
    rows = await _mgmt(token, "POST", f"/projects/{ref}/database/query/read-only", {"query": sql})
    return rows if isinstance(rows, list) else []


async def mgmt_projects(token: str) -> list[dict]:
    """
    The client's Supabase projects, to pick one.

    Returns:
        list[dict]: [{"ref", "name", "region"}].
    """
    projects = await _mgmt(token, "GET", "/projects")
    return [{"ref": p.get("ref") or p.get("id"), "name": p.get("name") or p.get("id"), "region": p.get("region")}
            for p in projects or []]


async def mgmt_list_tables(token: str, ref: str) -> list[dict]:
    """
    Like list_tables(), through the Management API: the tables and views with
    whether they have rows. One query lists them; ONE more query checks every
    table whose free estimate says 0 or unknown (views are left as "don't
    know" — checking a view can mean running it).

    Returns:
        list[dict]: same shape as list_tables().
    """
    systems = ", ".join(_literal(s) for s in SYSTEM_SCHEMAS)
    rows = await mgmt_query(token, ref, f"""
        SELECT t.table_schema AS schema, t.table_name AS name,
               CASE t.table_type WHEN 'VIEW' THEN 'view' ELSE 'table' END AS kind,
               coalesce(c.reltuples, -1)::bigint AS rows_estimate
        FROM information_schema.tables t
        LEFT JOIN pg_namespace n ON n.nspname = t.table_schema
        LEFT JOIN pg_class c ON c.relnamespace = n.oid AND c.relname = t.table_name
        WHERE t.table_schema NOT IN ({systems}) AND t.table_schema NOT LIKE 'pg\\_%'
        ORDER BY t.table_schema, t.table_name""")
    tables = [{"schema": r["schema"], "name": r["name"], "kind": r["kind"],
               "rows_estimate": max(int(r["rows_estimate"]), 0),
               "has_rows": True if r["kind"] == "table" and int(r["rows_estimate"]) > 0 else None} for r in rows]
    unsure = [i for i, t in enumerate(tables) if t["kind"] == "table" and t["has_rows"] is None][:EXISTS_CHECKS_MAX]
    if unsure:
        checks = " UNION ALL ".join(
            f"SELECT {i} AS i, EXISTS (SELECT 1 FROM {_ident(tables[i]['schema'])}.{_ident(tables[i]['name'])} LIMIT 1) AS has"
            for i in unsure)
        try:
            for r in await mgmt_query(token, ref, checks):
                tables[int(r["i"])]["has_rows"] = bool(r["has"])
        except HTTPException:
            pass   # e.g. one table not readable: leave them as "don't know"
    return tables


async def mgmt_read_table_csv(token: str, ref: str, schema: str, name: str, limit: int = MAX_ROWS,
                              max_bytes: int | None = None) -> dict:
    """
    Like read_table_csv(), through the Management API.

    Tables are read in CHUNK_ROWS pages in their physical order (PostgreSQL's
    row address `ctid`: "the rows after the last one I got"), so each request
    is small and nothing is read twice. Views get one capped read. The table
    must be one the project itself lists (the name is never trusted as typed).

    Returns:
        dict: {"csv": bytes, "rows": int, "columns": list[str], "truncated": bool}.

    Raises:
        HTTPException: 404 unknown table; 422 empty.
    """
    limit = max(1, min(int(limit), MAX_ROWS))
    if max_bytes is None:
        max_bytes = get_settings().max_upload_bytes - 64 * 1024
    known = {(t["schema"], t["name"]): t for t in await mgmt_list_tables(token, ref)}
    table = known.get((schema, name))
    if not table:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That table wasn't found in your project.")
    if table["has_rows"] is False:
        raise HTTPException(422, detail=f"{schema}.{name} has no rows yet, so there is nothing to analyze.")
    view = table["kind"] == "view"
    if view:
        limit = min(limit, MGMT_VIEW_MAX_ROWS)
    source = f"{_ident(schema)}.{_ident(name)}"
    out = io.BytesIO()
    rows, cut, more, after, columns = 0, False, False, None, []
    while True:
        want = (limit if view else min(CHUNK_ROWS, limit - rows)) + 1    # one extra row: is there more?
        if view:
            sql = f"SELECT * FROM {source} LIMIT {want}"
        else:
            where = f"WHERE ctid > {_literal(after)}::tid " if after else ""
            sql = f'SELECT ctid::text AS "__ctid", * FROM {source} {where}ORDER BY ctid LIMIT {want}'
        page = await mgmt_query(token, ref, sql)
        more = len(page) == want
        page = page[: want - 1] if more else page
        if not page:
            break
        if not view:
            after = page[-1]["__ctid"]
        chunk = pd.DataFrame(page).drop(columns=["__ctid"], errors="ignore")
        columns = columns or list(chunk.columns)
        text, fit, cut = csv_within(chunk, max_bytes - out.tell(), header=rows == 0)
        out.write(text)
        rows += fit
        if cut or not more or view or rows >= limit:
            break
    if rows == 0:
        raise HTTPException(422, detail=f"{schema}.{name} has rows too large to open here." if cut
                            else f"{schema}.{name} has no rows yet, so there is nothing to analyze.")
    return {"csv": out.getvalue(), "rows": rows, "columns": columns, "truncated": cut or more}



# ── Summaries in the client's database (api/services/pushdown.py) ────────────

async def run_select(src: dict, sql: str) -> tuple[list[str], list[list]]:
    """
    Run ONE generated SELECT (a pushdown summary or a catalog lookup) in the
    client's database, read-only; returns (column names, rows).

    `src` is the connections router's _supabase_source: a pasted connection
    string ("dsn" — PostgreSQL, Supabase or AWS Redshift's PostgreSQL endpoint)
    or "Sign in with Supabase" ("oauth", through the read-only query endpoint).
    """
    if src["mode"] == "dsn":
        conn = await _connect(src["dsn"])
        try:
            async with conn.transaction(readonly=True):
                recs = await conn.fetch(sql)
        except asyncpg.PostgresError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail=f"The database couldn't run that summary: {str(exc).splitlines()[0][:200]}")
        finally:
            await conn.close()
        cols = list(recs[0].keys()) if recs else []
        return cols, [list(r.values()) for r in recs]
    rows = await mgmt_query(src["token"], src["ref"], sql)
    cols = list(rows[0]) if rows else []
    return cols, [[r.get(c) for c in cols] for r in rows]


async def table_columns(src: dict, table: str) -> dict[str, str]:
    """
    A table's columns and their kinds ("number" / "date" / "text"), from the catalog.

    Raises:
        HTTPException: 404 when it isn't one of the client's tables (names are never trusted as typed).
    """
    from api.services.warehouses import kind_of   # here: warehouses imports this module

    schema, _, name = table.partition(".")
    _, rows = await run_select(src, "SELECT column_name, data_type FROM information_schema.columns "
                                    f"WHERE table_schema = {_literal(schema)} AND table_name = {_literal(name)} "
                                    "ORDER BY ordinal_position")
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That table wasn't found.")
    return {str(c): kind_of(str(t)) for c, t in rows}
