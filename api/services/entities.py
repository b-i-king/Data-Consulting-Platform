"""
Entities: each customer, vendor, product, payee and location of a business,
stored ONCE — and linked to every source that mentions it.

WHY THIS EXISTS
---------------
In fact.account_data every metric row carries its line's attributes. Before
entities, a customer's name, email and address were copied onto every row of
every invoice, and "Blue Door Cafe" from QuickBooks and "BLUE DOOR CAFE LLC"
from a spreadsheet were two unrelated pieces of text. Now (docs/DATA_MODEL.md,
"Entities"):

    fact.entities        one row per real customer / vendor / item / payee /
                         location: a display name and its details (email,
                         phone, address) — ONCE, merged from every source
    fact.entity_sources  one row per way a source refers to it ("QuickBooks
                         customer 58", "file_upload: name blue door cafe"),
                         with how it was matched
    fact.account_data    attributes hold `customer_link` (an entity_sources
                         id) instead of the name, ID and details

Storing the SOURCE's link (not the entity id) on each row means that merging
two customers later only re-points one entity_sources row: no stored metric
row is rewritten, and each source still gets its own spelling back.

HOW A LOAD IS LINKED (link())
-----------------------------
For each entity type, every distinct mention in the rows (by the source's own
ID, else the normalized name) is resolved, in order of confidence:
    1. already linked from this source            -> the same link
    2. same email, then same phone                -> that entity       ("email" / "phone")
    3. same normalized name                       -> that entity       ("name")
    4. fuzzy name >= AUTO_SCORE (92)              -> that entity       ("fuzzy")
    5. fuzzy name >= REVIEW_SCORE (80)            -> a NEW entity, with the other one
                                                     SUGGESTED for a person to confirm
    6. otherwise                                  -> a new entity      ("new")
(api/services/entity_matching.py does the normalizing: "Acme Bakery, LLC" ->
"acme bakery", emails lowercase, phones the last 10 digits.)
Then each row's attributes lose those fields and gain `<type>_link`, and the
row_hash is recomputed — stable, because the same mention always gets the same
link, so an unchanged reload still writes nothing.

READING BACK (expand())
-----------------------
"Use my stored data" puts each source's own name, ID and the entity's details
back in place of the link, so the table looks as it was loaded (details are
kept once per customer: the first value seen).

Like the other stores there is an in-memory path for development and tests.
"""

from __future__ import annotations

import json
import uuid

import pandas as pd
from rapidfuzz import fuzz

from api.core import database
from api.services import unified
from api.services.entity_matching import normalize_email, normalize_name, normalize_phone

AUTO_SCORE = 92     # same as entity_matching: at or above, the same entity
REVIEW_SCORE = 80   # at or above (below AUTO_SCORE): a person confirms

# Which unified attribute names describe each kind of entity.
ENTITY_TYPES: dict[str, dict] = {
    "customer": {"id": "customer_source_id", "name": "customer_name",
                 "details": ["customer_email", "customer_phone", "bill_address_line1", "bill_city", "bill_state",
                             "bill_postal_code"]},
    "vendor": {"id": "vendor_source_id", "name": "vendor_name", "details": ["vendor_email"]},
    "item": {"id": "item_source_id", "name": "item_name", "details": []},
    "payee": {"id": "payee_source_id", "name": "payee_name", "details": []},
    "location": {"id": None, "name": "location_name", "details": []},
}
_EMAIL_FIELDS = {"customer_email", "vendor_email"}
_PHONE_FIELDS = {"customer_phone"}

# ── In-memory fallback (development only) ────────────────────────────────────
_entities: dict[int, dict] = {}   # entity_id -> row
_links: dict[int, dict] = {}      # link_id -> row
_ids = {"entity": 0, "link": 0}


def link_field(entity_type: str) -> str:
    """The attribute that holds the link: "customer" -> "customer_link"."""
    return f"{entity_type}_link"


def _mention_key(entity_type: str, attrs: dict) -> tuple[str, str | None, str | None] | None:
    """
    How this line refers to an entity of one type: (source_key, source_id, label), or None.

    The source's own ID wins ("id:58"); without one, the normalized name
    ("name:blue door cafe"). A line with neither mentions no entity.
    """
    spec = ENTITY_TYPES[entity_type]
    source_id = attrs.get(spec["id"]) if spec["id"] else None
    label = attrs.get(spec["name"])
    source_id = None if unified._is_missing(source_id) else str(source_id)
    label = None if unified._is_missing(label) else str(label)
    if source_id:
        return f"id:{source_id}", source_id, label
    norm = normalize_name(label) if label else ""
    return (f"name:{norm}", None, label) if norm else None


# ── Reading and writing the two tables ───────────────────────────────────────

async def _load(aid: str) -> tuple[list[dict], list[dict]]:
    """Every entity and link of one account (a small business has hundreds, not millions)."""
    if database.pool is None:
        return ([dict(e) for e in _entities.values() if e["account_id"] == aid],
                [dict(link) for link in _links.values() if link["account_id"] == aid])
    async with database.pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.account_id', $1, true)", aid)
        ents = await conn.fetch("SELECT entity_id, entity_type, display_name, details::text AS details "
                                "FROM fact.entities WHERE account_id = $1", uuid.UUID(aid))
        links = await conn.fetch("SELECT * FROM fact.entity_sources WHERE account_id = $1", uuid.UUID(aid))
    out = []
    for e in ents:
        row = dict(e)
        row["details"] = json.loads(row["details"] or "{}")
        out.append(row)
    return out, [dict(r) for r in links]


async def _save(aid: str, new_entities: list[dict], new_links: list[dict], detail_updates: dict[int, dict]) -> None:
    """
    Write new entities (they get real ids), new links, and details for existing
    entities (only fields they don't have yet), in ONE transaction.
    New entities and links use temporary negative ids, replaced here.
    """
    if database.pool is None:
        real: dict[int, int] = {}
        for e in new_entities:
            _ids["entity"] += 1
            real[e["entity_id"]] = _ids["entity"]
            _entities[_ids["entity"]] = {**e, "entity_id": _ids["entity"], "account_id": aid}
        for link in new_links:
            _ids["link"] += 1
            fixed = {**link, "account_id": aid, "entity_id": real.get(link["entity_id"], link["entity_id"]),
                     "suggested_entity_id": real.get(link["suggested_entity_id"], link["suggested_entity_id"])}
            link["link_id"] = _ids["link"]
            _links[_ids["link"]] = {**fixed, "link_id": _ids["link"]}
        for eid, details in detail_updates.items():
            eid = real.get(eid, eid)
            if eid in _entities:
                _entities[eid]["details"] = {**details, **_entities[eid]["details"]}
        return
    async with database.pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.account_id', $1, true)", aid)
        real = {}
        for e in new_entities:
            real[e["entity_id"]] = await conn.fetchval(
                """INSERT INTO fact.entities (account_id, entity_type, display_name, details)
                   VALUES ($1, $2, $3, $4::jsonb) RETURNING entity_id""",
                uuid.UUID(aid), e["entity_type"], e["display_name"], json.dumps(e["details"], default=str))
        for link in new_links:
            link["link_id"] = await conn.fetchval(
                """INSERT INTO fact.entity_sources (account_id, entity_id, entity_type, source_system, source_key,
                                                    source_id, source_label, match_method, match_score,
                                                    suggested_entity_id)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                   ON CONFLICT (account_id, entity_type, source_system, source_key)
                   DO UPDATE SET source_label = COALESCE(fact.entity_sources.source_label, EXCLUDED.source_label)
                   RETURNING link_id""",
                uuid.UUID(aid), real.get(link["entity_id"], link["entity_id"]), link["entity_type"],
                link["source_system"], link["source_key"], link["source_id"], link["source_label"],
                link["match_method"], link["match_score"],
                real.get(link["suggested_entity_id"], link["suggested_entity_id"]))
        for eid, details in detail_updates.items():
            await conn.execute(
                # The existing details win: a new source only fills what was missing.
                "UPDATE fact.entities SET details = $2::jsonb || details, updated_at = now() "
                "WHERE entity_id = $1 AND account_id = $3",
                real.get(eid, eid), json.dumps(details, default=str), uuid.UUID(aid))


# ── Matching ─────────────────────────────────────────────────────────────────

def _best_match(entity_type: str, label: str | None, details: dict, pool: list[dict]) -> tuple[dict | None, str, int]:
    """
    The existing entity this mention most likely is: (entity or None, method, score).

    Order: exact email -> exact phone -> exact normalized name -> fuzzy name.
    """
    same_type = [e for e in pool if e["entity_type"] == entity_type]
    for fields, norm, method in ((_EMAIL_FIELDS, normalize_email, "email"), (_PHONE_FIELDS, normalize_phone, "phone")):
        mine = next((norm(details[f]) for f in fields if details.get(f)), "")
        if mine:
            for e in same_type:
                if any(norm(e["details"].get(f)) == mine for f in fields if e["details"].get(f)):
                    return e, method, 100
    name = normalize_name(label) if label else ""
    if not name:
        return None, "new", 0
    best, best_score = None, 0
    for e in same_type:
        other = normalize_name(e["display_name"]) if e["display_name"] else ""
        if not other:
            continue
        if other == name:
            return e, "name", 100
        score = int(fuzz.token_sort_ratio(name, other))
        if score > best_score:
            best, best_score = e, score
    if best is not None and best_score >= AUTO_SCORE:
        return best, "fuzzy", best_score
    if best is not None and best_score >= REVIEW_SCORE:
        return best, "review", best_score
    return None, "new", 0


async def link(aid: str, source_system: str, rows: pd.DataFrame, *, write: bool = True) -> tuple[pd.DataFrame, dict]:
    """
    Resolve every customer / vendor / item / payee / location in unified rows
    to an entity, and replace their fields in the attributes with a link.

    Args:
        aid: str, the account (plan holder).
        source_system: str, "file_upload", "quickbooks", "xero".
        rows: unified rows (UNIFIED_COLUMNS), attributes as dicts.
        write: False = a dry run (nothing saved; the rows are not changed) —
            used to preview what a load would add.

    Returns:
        tuple[pd.DataFrame, dict]: (the rows, linked when write=True; stats
        {"entities_added", "links_added", "matches_to_review"}).

    Example:
        >>> rows, stats = await link(aid, "quickbooks", rows)
        >>> rows["attributes"].iloc[0]["customer_link"], stats["entities_added"]
        (12, 1)
    """
    stats = {"entities_added": 0, "links_added": 0, "matches_to_review": 0}
    if rows.empty:
        return rows, stats
    entities, links = await _load(aid)
    by_key = {(lk["entity_type"], lk["source_key"]): lk for lk in links if lk["source_system"] == source_system}
    pool = list(entities)
    new_entities: list[dict] = []
    new_links: list[dict] = []
    detail_updates: dict[int, dict] = {}
    resolved: dict[tuple[str, str], dict] = {}   # (type, key) -> the link row (new or old)
    temp = 0

    # 1. Every distinct mention, with the first value seen of each detail.
    mentions: dict[tuple[str, str], dict] = {}
    for attrs in rows["attributes"]:
        attrs = attrs or {}
        for etype, spec in ENTITY_TYPES.items():
            found = _mention_key(etype, attrs)
            if not found:
                continue
            key, source_id, label = found
            m = mentions.setdefault((etype, key), {"source_id": source_id, "label": label, "details": {}})
            m["label"] = m["label"] or label
            for f in spec["details"]:
                v = attrs.get(f)
                if f not in m["details"] and not unified._is_missing(v):
                    m["details"][f] = v

    # 2. Resolve each mention to an entity.
    for (etype, key), m in mentions.items():
        old = by_key.get((etype, key))
        if old:
            resolved[(etype, key)] = old
            if m["details"]:
                detail_updates[old["entity_id"]] = {**m["details"], **detail_updates.get(old["entity_id"], {})}
            continue
        entity, method, score = _best_match(etype, m["label"], m["details"], pool)
        suggested = None
        if entity is None or method == "review":
            temp -= 1
            suggested = entity["entity_id"] if entity else None
            entity = {"entity_id": temp, "entity_type": etype, "display_name": m["label"] or m["source_id"],
                      "details": dict(m["details"])}
            new_entities.append(entity)
            pool.append(entity)
            stats["entities_added"] += 1
            stats["matches_to_review"] += 1 if suggested else 0
            method = "review" if suggested else "new"
        elif m["details"]:
            detail_updates[entity["entity_id"]] = {**m["details"], **detail_updates.get(entity["entity_id"], {})}
        row = {"entity_id": entity["entity_id"], "entity_type": etype, "source_system": source_system,
               "source_key": key, "source_id": m["source_id"], "source_label": m["label"],
               "match_method": method, "match_score": score or None, "suggested_entity_id": suggested,
               "link_id": None}
        new_links.append(row)
        resolved[(etype, key)] = row
        stats["links_added"] += 1

    if not write:
        return rows, stats
    await _save(aid, new_entities, new_links, detail_updates)

    # 3. Replace the fields with the link, and recompute each row's fingerprint.
    def relink(attrs: dict) -> dict:
        attrs = dict(attrs or {})
        for etype, spec in ENTITY_TYPES.items():
            found = _mention_key(etype, attrs)
            if not found:
                continue
            for f in [spec["id"], spec["name"], *spec["details"]]:
                if f:
                    attrs.pop(f, None)
            attrs[link_field(etype)] = int(resolved[(etype, found[0])]["link_id"])
        return attrs

    out = rows.copy()
    out["attributes"] = out["attributes"].map(relink)
    out["row_hash"] = [unified.row_hash(r) for r in out[["metric_type", "metric_subtype", "metric_unit",
                                                         "metric_value", "event_date", "attributes"]]
                       .to_dict("records")]
    return out, stats


# ── Reading back ─────────────────────────────────────────────────────────────

async def lookup(aid: str) -> dict[int, dict]:
    """link_id -> {"entity_type", "entity_id", "source_id", "source_label", "details"} (for expand())."""
    entities, links = await _load(aid)
    by_id = {e["entity_id"]: e for e in entities}
    return {lk["link_id"]: {"entity_type": lk["entity_type"], "entity_id": lk["entity_id"],
                            "source_id": lk["source_id"], "source_label": lk["source_label"],
                            "details": by_id.get(lk["entity_id"], {}).get("details", {}),
                            "display_name": by_id.get(lk["entity_id"], {}).get("display_name")}
            for lk in links}


def expand(attrs: dict, links: dict[int, dict]) -> dict:
    """
    Put each source's own name and ID, and the entity's details, back in place of its link.

    Example:
        >>> expand({"customer_link": 12, "store": "Midtown"}, {12: {"entity_type": "customer",
        ...     "source_id": None, "source_label": "Blue Door Cafe", "details": {"customer_email": "bd@x.com"}}})
        {'store': 'Midtown', 'customer_name': 'Blue Door Cafe', 'customer_email': 'bd@x.com'}
    """
    out = {}
    for k, v in (attrs or {}).items():
        if not k.endswith("_link") or k[:-5] not in ENTITY_TYPES:
            out[k] = v
            continue
        info = links.get(int(v)) if v is not None else None
        if not info:
            continue
        spec = ENTITY_TYPES[k[:-5]]
        if spec["id"] and info["source_id"]:
            out[spec["id"]] = info["source_id"]
        if info["source_label"]:
            out[spec["name"]] = info["source_label"]
        for f in spec["details"]:
            if f in info["details"]:
                out[f] = info["details"][f]
    return out


async def link_to_entity(aid: str) -> dict[int, int]:
    """link_id -> entity_id (to group stored rows by the REAL customer across sources)."""
    return {k: v["entity_id"] for k, v in (await lookup(aid)).items()}


# ── "Is this the same customer?" (the review screen) ─────────────────────────

async def pending_reviews(aid: str) -> list[dict]:
    """
    Name matches waiting for a person: a mention that looks like an existing
    entity (score 80–91), kept separate until someone decides.

    Returns:
        list[dict]: [{"link_id", "entity_type", "source_system", "name", "score",
        "candidate": {"entity_id", "name", "sources": ["quickbooks", ...]}}].
    """
    entities, links = await _load(aid)
    by_id = {e["entity_id"]: e for e in entities}
    out = []
    for lk in links:
        cand = by_id.get(lk.get("suggested_entity_id"))
        if not cand:
            continue
        out.append({"link_id": lk["link_id"], "entity_type": lk["entity_type"], "source_system": lk["source_system"],
                    "name": lk["source_label"] or lk["source_id"], "score": lk["match_score"],
                    "candidate": {"entity_id": cand["entity_id"], "name": cand["display_name"],
                                  "sources": sorted({o["source_system"] for o in links
                                                     if o["entity_id"] == cand["entity_id"]})}})
    return sorted(out, key=lambda r: (r["entity_type"], -(r["score"] or 0)))


async def decide(aid: str, link_id: int, same: bool) -> bool:
    """
    A person's answer to "Is this the same customer?".

    same=True: the link moves to the suggested entity (its details fill the
    gaps), and the entity it leaves is deleted if nothing else links to it.
    same=False: the suggestion is cleared; they stay separate.
    No stored metric row changes either way (rows hold the link, not the entity).

    Returns:
        bool: False when the link isn't this account's or has no suggestion.
    """
    entities, links = await _load(aid)
    lk = next((x for x in links if x["link_id"] == link_id), None)
    if not lk or not lk.get("suggested_entity_id"):
        return False
    target, old = lk["suggested_entity_id"], lk["entity_id"]
    orphan = same and not any(x["entity_id"] == old and x["link_id"] != link_id for x in links)
    old_details = next((e["details"] for e in entities if e["entity_id"] == old), {})
    if database.pool is None:
        row = _links[link_id]
        if same:
            row.update(entity_id=target, match_method="confirmed", match_score=100)
            _entities[target]["details"] = {**old_details, **_entities[target]["details"]}
            if orphan:
                _entities.pop(old, None)
        row["suggested_entity_id"] = None
        return True
    async with database.pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.account_id', $1, true)", aid)
        if same:
            await conn.execute(
                """UPDATE fact.entity_sources SET entity_id = $2, match_method = 'confirmed', match_score = 100,
                          suggested_entity_id = NULL WHERE link_id = $1 AND account_id = $3""",
                link_id, target, uuid.UUID(aid))
            await conn.execute("UPDATE fact.entities SET details = $2::jsonb || details WHERE entity_id = $1",
                               target, json.dumps(old_details, default=str))
            if orphan:
                await conn.execute("DELETE FROM fact.entities WHERE entity_id = $1 AND account_id = $2", old, uuid.UUID(aid))
        else:
            await conn.execute("UPDATE fact.entity_sources SET suggested_entity_id = NULL WHERE link_id = $1 "
                               "AND account_id = $2", link_id, uuid.UUID(aid))
    return True


async def delete_all(aid: str) -> None:
    """Delete every entity and link of an account ("delete my stored data")."""
    if database.pool is None:
        for k in [k for k, v in _links.items() if v["account_id"] == aid]:
            del _links[k]
        for k in [k for k, v in _entities.items() if v["account_id"] == aid]:
            del _entities[k]
        return
    async with database.pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.account_id', $1, true)", aid)
        await conn.execute("DELETE FROM fact.entity_sources WHERE account_id = $1", uuid.UUID(aid))
        await conn.execute("DELETE FROM fact.entities WHERE account_id = $1", uuid.UUID(aid))
