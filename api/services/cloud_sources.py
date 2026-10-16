"""
Cloud data sources: a client's OWN database, read where it lives (never stored).

WHY THIS EXISTS
---------------
Clients whose data is already in a cloud database (Supabase or any
PostgreSQL) already have a system that manages it. They come for quick
analysis, dashboards, ML and AI — not for us to keep a copy. So this module:

  * checks and saves their database login (Settings -> Integrations ->
    "Supabase database"), ENCRYPTED;
  * lists their tables;
  * reads ONE table (up to MAX_ROWS rows) into pandas when they open it in the
    workspace, inside a READ ONLY transaction, and hands it back as a CSV
    file — the same way "Use my stored data" works. Nothing from it is stored
    on our side; Data Engineering's "Load into database" is switched off for
    cloud data, and Data Cleaning only REPORTS on it (it doesn't change it).

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
import ipaddress
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


async def list_tables(dsn: str) -> list[dict]:
    """
    The client's tables and views (system schemas left out), with a rough row count.

    Args:
        dsn: str, the connection string (decrypted).

    Returns:
        list[dict]: [{"schema", "name", "kind" ("table" | "view"), "rows_estimate"}],
        sorted by schema then name.

    Example:
        >>> await list_tables(dsn)
        [{'schema': 'public', 'name': 'orders', 'kind': 'table', 'rows_estimate': 18210}]
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
    finally:
        await conn.close()
    return [{**dict(r), "rows_estimate": max(int(r["rows_estimate"]), 0)} for r in rows]


def _ident(name: str) -> str:
    """Quote a SQL identifier ("Orders" -> "\\"Orders\\""), doubling any quote inside it."""
    return '"' + name.replace('"', '""') + '"'


async def read_table(dsn: str, schema: str, name: str, limit: int = MAX_ROWS) -> pd.DataFrame:
    """
    Read one table (or view) into pandas, read-only, up to `limit` rows.

    The table must be one list_tables() returned — the name is never trusted
    as typed.

    Args:
        dsn: str, the connection string (decrypted).
        schema: str, e.g. "public".
        name: str, e.g. "orders".
        limit: int, the most rows (1 to MAX_ROWS).

    Returns:
        pd.DataFrame: the rows (UUIDs, dates and JSON become text/ISO so they
        travel as CSV).

    Raises:
        HTTPException: 404 when it isn't one of the client's tables.

    Example:
        >>> (await read_table(dsn, "public", "orders", 1000)).shape
        (1000, 7)
    """
    limit = max(1, min(int(limit), MAX_ROWS))
    tables = {(t["schema"], t["name"]) for t in await list_tables(dsn)}
    if (schema, name) not in tables:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That table wasn't found in your database.")
    conn = await _connect(dsn)
    try:
        async with conn.transaction(readonly=True):
            await conn.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
            stmt = await conn.prepare(f"SELECT * FROM {_ident(schema)}.{_ident(name)} LIMIT $1")
            columns = [a.name for a in stmt.get_attributes()]
            records = await stmt.fetch(limit)
    finally:
        await conn.close()
    df = pd.DataFrame([tuple(r) for r in records], columns=columns)
    for col in df.columns:
        if df[col].dtype == object:
            # UUIDs, JSON (dict/list) and other objects become text for the CSV.
            df[col] = df[col].map(lambda v: v if v is None or isinstance(v, (str, int, float)) else str(v))
    return df
