"""
partners — the home page's "Partners" carousel: who may appear, and how.

A business on a paid plan can ask to have its logo on the home page, linked
to its website (a little promotion, and social proof for the platform). The
rules are shown to the business UP FRONT (REQUIREMENTS) so nothing is a
surprise:

  1. A BUSINESS account with a business name.
  2. A paid plan (Starter or above, monthly or annual) that has been live for
     at least MIN_PAID_DAYS days.
  3. The business's OWN website domain (not a free page on a site builder or a
     social network), and the logo links to it.
  4. A clean, family-friendly logo (no vulgar or sexual images, no promotions
     beyond the logo itself) — every logo is checked by a person before it
     goes live.
  5. Shown only while the plan is paid: canceling, or deleting the account,
     takes the logo off the home page automatically.

Automatic checks (at submission, saved in `checks` for the admin):
  * paid_days            — how long the plan has been live (must be >= MIN_PAID_DAYS)
  * own_domain           — the website isn't a free subdomain / social page (required)
  * website_reachable    — the website answers (a flag for the admin, not a blocker:
                           a site can be briefly down)
  * email_matches_website — the account's email is on the website's domain
                           (e.g. ana@sunrisebakery.com and sunrisebakery.com):
                           a strong sign it's really their business (a flag)
  * linkedin_given       — a LinkedIn company page was given (a flag)
The FINAL approval is always an admin's (Settings -> Administration ->
Partners): they look at the logo and the flags. Images can't be judged
reliably by code, so a person decides.

Admins can also ADD a partner themselves (`add_by_admin`): a company they
work with directly and invoice outside the platform, with no account. It's
live at once (the admin vouches for it) and stays until an admin removes it.

The logo: PNG, JPEG or WebP up to MAX_UPLOAD_MB; it's opened with Pillow
(never SVG — an SVG can carry scripts). It must be ONLY the logo, on a
transparent background (`_transparent_background`): one that already has one
is kept; a plain one-color background (a white box, say) is removed
automatically — including the holes inside letters and marks (the inside of
an "o"), because the website draws logos as one-color silhouettes, where a
leftover white hole would show as a solid blob; a busy background (a photo,
a gradient) is refused.
Empty margins are trimmed so every logo sizes the same. The website then
draws each logo as a one-color silhouette in the theme's colors
(web/components/home/ThemedLogo.tsx). Finally it's resized to fit LOGO_BOX
(320 x 160 px) and saved as WebP — usually 5-20 KB, so a carousel of 30 logos is lighter
than one photo. Stored in dim.partners (see docs/ARCHITECTURE.md, "Partner logos").
"""

from __future__ import annotations

import io
import random
import re
from datetime import date, datetime, timezone
from urllib.parse import urlparse

import httpx
import numpy as np
from fastapi import HTTPException, status
from PIL import Image, UnidentifiedImageError

from api.services import account_store, billing, entitlements

MIN_PAID_DAYS = 30                 # how long the plan must have been live before applying
MAX_UPLOAD_MB = 2
LOGO_BOX = (320, 160)              # the logo is resized to fit inside this box (px)
MIN_LOGO_WIDTH = 100               # smaller than this looks blurry once shown
BACKGROUND_TOLERANCE = 28          # how far (0-255 per color) a pixel may be from the background color and still count as it
WORK_SIZE = (1280, 1280)           # large uploads are shrunk to this before the background check (keeps it fast)
MAX_SHOWN = 30                     # at most this many logos in the carousel at once (a daily rotation)
Image.MAX_IMAGE_PIXELS = 25_000_000   # refuse "image bombs" (a tiny file that unpacks to a huge picture)

REQUIREMENTS = [
    "A business account with your business name.",
    f"A paid plan (Starter or above, monthly or annual), live for at least {MIN_PAID_DAYS} days.",
    "Your own website domain (e.g. yourbusiness.com — not a free page on a site builder or a social network). "
    "Clicking your logo opens it.",
    "A clean, family-friendly logo with a transparent background (PNG or WebP) — just the logo, no ads or "
    "slogans added (a plain one-color background is removed for you). No vulgar or sexual images: a person "
    "checks every logo before it goes live.",
    "Your logo is shown only while your plan is paid: if you cancel or delete your account, it comes off the "
    "home page automatically.",
]
# Free email services: an email here says nothing about owning a business domain.
FREE_EMAIL_DOMAINS = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "live.com", "aol.com", "icloud.com",
                      "me.com", "msn.com", "proton.me", "protonmail.com", "gmx.com", "zoho.com", "yandex.com",
                      "mail.com"}
# Websites that aren't the business's own domain (free pages and social profiles).
NOT_OWN_DOMAIN = ("wixsite.com", "squarespace.com", "godaddysites.com", "square.site", "carrd.co", "linktr.ee",
                  "facebook.com", "instagram.com", "tiktok.com", "x.com", "twitter.com", "linkedin.com",
                  "youtube.com", "etsy.com", "weebly.com", "wordpress.com", "blogspot.com", "github.io",
                  "vercel.app", "netlify.app", "google.com", "sites.google.com", "business.site", "yelp.com")
LINKEDIN_COMPANY = re.compile(r"^https://(www\.)?linkedin\.com/company/[A-Za-z0-9_\-%.]+/?$")


# ── Small helpers ─────────────────────────────────────────────────────────────

def normalize_website(url: str) -> str:
    """
    "sunrisebakery.com" or "http://www.sunrisebakery.com/" -> "https://www.sunrisebakery.com".

    Raises:
        HTTPException: 400 when it isn't a web address.
    """
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if "." not in host or not re.fullmatch(r"[a-z0-9.-]+", host):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Please enter your website, e.g. yourbusiness.com.")
    path = parsed.path.rstrip("/")
    return f"https://{host}{path}"


def site_domain(url: str) -> str:
    """The website's domain without "www.": "https://www.sunrisebakery.com/menu" -> "sunrisebakery.com"."""
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def own_domain(url: str) -> bool:
    """True when the website is on the business's own domain (not a site builder's or a social network's)."""
    host = site_domain(url)
    return not any(host == d or host.endswith("." + d) for d in NOT_OWN_DOMAIN)


async def check_website(url: str) -> bool:
    """True when the website answers (any status below 400) within 5 seconds. Never raises."""
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
            res = await client.get(url, headers={"User-Agent": "DataConsultingPlatform-PartnerCheck/1.0"})
            return res.status_code < 400
    except Exception:   # noqa: BLE001 — unreachable, bad certificate, timeout: all just "no"
        return False


def _edges(a: np.ndarray) -> np.ndarray:
    """The outermost ring of pixels of an image array (top, bottom, left and right rows)."""
    return np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])


def _transparent_background(img: Image.Image) -> tuple[Image.Image, str]:
    """
    Make sure the logo sits on a transparent background; return (image, how).

    * Most of the edge already transparent -> kept as is ("transparent").
    * The edge is (almost) all ONE color -> that color is made transparent
      everywhere: around the logo AND in its holes (the inside of an "o"),
      since the site draws each logo as a one-color silhouette ("removed").
    * Anything else (a photo, a gradient, a busy pattern) -> 400: we can't
      tell the logo from the background reliably.

    Raises:
        HTTPException: 400 a busy background, or nothing left after removing it.
    """
    a = np.asarray(img).copy()                       # height x width x 4 (RGBA), values 0-255
    alpha = a[..., 3]
    if (_edges(alpha) < 16).mean() >= 0.6:
        return img, "transparent"
    rgb = a[..., :3].astype(int)
    edge = _edges(rgb)
    bg = np.median(edge, axis=0)                     # the background color: the edge's typical color
    if (np.abs(edge - bg).max(axis=1) <= BACKGROUND_TOLERANCE).mean() < 0.9:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="Please send your logo with a transparent background (a PNG or WebP with just "
                                   "the logo). We couldn't remove this background automatically.")
    background = np.abs(rgb - bg).max(axis=2) <= BACKGROUND_TOLERANCE   # every pixel of the background color
    a[background, 3] = 0
    if (a[..., 3] > 0).mean() < 0.005:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="We couldn't find a logo in that image. Please send just the logo on a "
                                   "transparent background.")
    return Image.fromarray(a, "RGBA"), "removed"


def process_logo(raw: bytes) -> tuple[bytes, int, int, str]:
    """
    Check an uploaded logo, keep only the logo (transparent background, margins
    trimmed) and turn it into a small WebP that fits LOGO_BOX.

    Args:
        raw: bytes, the uploaded file (PNG, JPEG or WebP).

    Returns:
        tuple[bytes, int, int, str]: (WebP bytes, width, height, background) —
        background "transparent" (it already was) or "removed" (a plain one was taken off).

    Raises:
        HTTPException: 413 over MAX_UPLOAD_MB; 400 not a PNG / JPEG / WebP image, too small,
            or a background that can't be removed.

    Example:
        >>> webp, w, h, background = process_logo(open("logo.png", "rb").read())
        >>> w <= 320 and h <= 160
        True
    """
    if len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail=f"Please use a logo file under {MAX_UPLOAD_MB} MB.")
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            probe.verify()                         # a damaged or disguised file fails here
        img = Image.open(io.BytesIO(raw))
        if img.format not in ("PNG", "JPEG", "WEBP"):
            raise UnidentifiedImageError
        img.seek(0)                                # an animated WebP / PNG: the first frame only
        img = img.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="Please upload your logo as a PNG, JPEG or WebP image (SVG isn't accepted).")
    if img.width < MIN_LOGO_WIDTH:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"That logo is too small — please use one at least {MIN_LOGO_WIDTH} pixels wide.")
    img.thumbnail(WORK_SIZE, Image.LANCZOS)        # big uploads: shrink first, so the checks below stay fast
    img, background = _transparent_background(img)
    box = img.getchannel("A").getbbox()            # trim empty margins: every logo then sizes the same
    if box:
        img = img.crop(box)
    img.thumbnail(LOGO_BOX, Image.LANCZOS)         # keeps the shape; only ever shrinks
    out = io.BytesIO()
    img.save(out, "WEBP", quality=82, method=6)    # the transparency is kept
    return out.getvalue(), img.width, img.height, background


def logo_url(row: dict) -> str:
    """The logo's address; the version changes with each submission, so browsers can cache it for a long time."""
    v = int(row["submitted_at"].timestamp())
    return f"/api/partners/{row['id']}/logo?v={v}"


async def _paid_days(holder_id: str) -> int:
    """Days since the plan holder's current live subscription started (0 without one)."""
    _, subs = await billing.live_plan_services(holder_id)
    if not subs:
        return 0
    started = min(s["created_at"] for s in subs)
    return (datetime.now(timezone.utc) - started).days


# ── For the business (Settings -> Account) ───────────────────────────────────

async def status_for(account: dict) -> dict:
    """
    What the business sees: the requirements, whether it can apply now, and its application.

    Returns:
        dict: {"requirements": [...], "eligible": bool, "missing": [plain-English reasons],
        "paid_days", "min_paid_days", "application": None or {"status", "reason", "business_name",
        "website_url", "linkedin_url", "logo_url", "checks", "submitted_at"}}.
    """
    holder = await entitlements.plan_holder(account)
    hid = str(holder["id"])
    days = await _paid_days(hid)
    missing = []
    if holder.get("account_type") != "business" or not holder.get("company_name"):
        missing.append("A business account with your business name (Settings -> Account).")
    if days == 0:
        missing.append("A paid plan (Starter or above).")
    elif days < MIN_PAID_DAYS:
        missing.append(f"Your plan has been live {days} day{'s' if days != 1 else ''}; partners need "
                       f"{MIN_PAID_DAYS}. You can apply in {MIN_PAID_DAYS - days} more.")
    row = await account_store.get_partner(hid)
    application = None
    if row:
        application = {k: row.get(k) for k in ("status", "reason", "business_name", "website_url", "linkedin_url",
                                               "checks", "submitted_at", "logo_width", "logo_height")}
        application["logo_url"] = logo_url(row)
    return {"requirements": REQUIREMENTS, "eligible": not missing, "missing": missing, "paid_days": days,
            "min_paid_days": MIN_PAID_DAYS, "application": application}


async def apply(account: dict, website_url: str, linkedin_url: str | None, logo_raw: bytes, agreed: bool) -> dict:
    """
    Submit (or resubmit) the logo and website; it waits for an admin.

    Raises:
        HTTPException: 400 not agreed / bad website / not their own domain / bad LinkedIn / bad
            logo; 403 not eligible yet (the reasons); 413 logo too big.
    """
    if not agreed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Please confirm you've read the partner requirements.")
    info = await status_for(account)
    if not info["eligible"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=" ".join(info["missing"]))
    site = normalize_website(website_url)
    if not own_domain(site):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="Partners need their own website domain (e.g. yourbusiness.com), not a free page "
                                   "on a site builder or a social network.")
    linkedin = (linkedin_url or "").strip() or None
    if linkedin and not LINKEDIN_COMPANY.match(linkedin):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="The LinkedIn link should be your company page: https://www.linkedin.com/company/...")
    webp, w, h, background = process_logo(logo_raw)
    holder = await entitlements.plan_holder(account)
    email_domain = str(holder.get("email", "")).rsplit("@", 1)[-1].lower()
    checks = {
        "paid_days": info["paid_days"],
        "own_domain": True,
        "website_reachable": await check_website(site),
        "email_matches_website": email_domain not in FREE_EMAIL_DOMAINS and (
            email_domain == site_domain(site) or email_domain.endswith("." + site_domain(site))),
        "linkedin_given": bool(linkedin),
        "logo_bytes": len(webp),
        "background": background,              # "transparent" (as sent) or "removed" (a plain one taken off)
    }
    row = await account_store.save_partner(str(holder["id"]), business_name=holder["company_name"], website_url=site,
                                           linkedin_url=linkedin, logo_webp=webp, logo_width=w, logo_height=h,
                                           checks=checks)
    return (await status_for(account)) | {"submitted": row["status"]}


async def withdraw(account: dict) -> dict:
    """Take the logo off (or cancel the application). 404 if they never applied."""
    holder = await entitlements.plan_holder(account)
    row = await account_store.get_partner(str(holder["id"]))
    if not row or not await account_store.set_partner_status(row["id"], "withdrawn"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="There's no partner application to withdraw.")
    return await status_for(account)


# ── For admins (Settings -> Administration -> Partners) ──────────────────────

async def admin_list(status_filter: str | None = None) -> list[dict]:
    """Partners (newest first): applications to review, and the ones on the page, with the checks and logo address."""
    rows = await account_store.list_partners(status_filter)
    return [{**{k: r.get(k) for k in ("id", "account_id", "source", "business_name", "website_url", "linkedin_url",
                                      "status", "checks", "reason", "submitted_at", "decided_at")},
             "logo_url": logo_url(r), "logo_width": r["logo_width"], "logo_height": r["logo_height"]} for r in rows]


async def decide(admin_id: str | None, partner_id: str, approve: bool, reason: str | None) -> dict:
    """
    Approve (it goes live while the plan is paid) or turn down (a reason is required; the business sees it).

    Raises:
        HTTPException: 400 a turn-down without a reason; 404 no such partner.
    """
    reason = (reason or "").strip() or None
    if not approve and not reason:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Please give the business a reason.")
    row = await account_store.set_partner_status(partner_id, "approved" if approve else "rejected",
                                                 None if approve else reason, admin_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such partner.")
    return {"id": partner_id, "status": row["status"], "reason": row.get("reason")}


async def add_by_admin(admin_id: str | None, business_name: str, website_url: str, logo_raw: bytes) -> dict:
    """
    Add a company the admin works with directly (invoiced outside the platform, no account): live at once.

    The admin vouches for it, so there's no paid-plan rule; it stays until an
    admin removes it. The website and logo get the same checks as everyone's
    (a real web address; a PNG / JPEG / WebP turned into a small WebP).

    Raises:
        HTTPException: 400 no name / bad website / bad logo; 413 logo too big.
    """
    name = (business_name or "").strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Please enter the company's name.")
    site = normalize_website(website_url)
    webp, w, h, _ = process_logo(logo_raw)
    row = await account_store.add_admin_partner(business_name=name[:120], website_url=site, logo_webp=webp,
                                                logo_width=w, logo_height=h, admin_id=admin_id)
    return {**{k: row.get(k) for k in ("id", "source", "business_name", "website_url", "status")},
            "logo_url": logo_url(row)}


async def remove(partner_id: str) -> None:
    """
    Take a partner off the list for good (an admin-added company, or any partner an admin removes).

    Raises:
        HTTPException: 404 no such partner.
    """
    if not await account_store.delete_partner(partner_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such partner.")


# ── The public carousel and logos ─────────────────────────────────────────────

async def _showing(row: dict) -> bool:
    """
    Is an approved partner on the page? An admin-added one, yes (until removed);
    a business that applied, only while its plan holder has a live paid subscription.
    """
    if row["status"] != "approved":
        return False
    return row.get("source") == "admin" or bool((await billing.live_plan_services(row["account_id"]))[1])


async def public_list(today: date | None = None) -> dict:
    """
    The carousel: partners on the page, at most MAX_SHOWN, in a different
    order each day (so every partner gets time at the front).

    Returns:
        dict: {"partners": [{"id", "name", "url", "logo_url", "width", "height"}], "requirements": [...]}.
    """
    shown = [r for r in await account_store.list_partners("approved") if await _showing(r)]
    random.Random((today or date.today()).toordinal()).shuffle(shown)   # same order all day, new order tomorrow
    return {"partners": [{"id": r["id"], "name": r["business_name"], "url": r["website_url"],
                          "logo_url": logo_url(r), "width": r["logo_width"], "height": r["logo_height"]}
                         for r in shown[:MAX_SHOWN]],
            "requirements": REQUIREMENTS}


async def logo(partner_id: str, viewer_account_id: str | None = None, admin: bool = False) -> bytes:
    """
    The logo's WebP bytes: public for a partner on the page; an admin, or the
    business itself (`viewer_account_id`), can also see one waiting for review.

    Raises:
        HTTPException: 404 when there's nothing to show.
    """
    row = await account_store.get_partner_by_id(partner_id)
    own = bool(row) and viewer_account_id is not None and row.get("account_id") == viewer_account_id
    if not row or not (admin or own or await _showing(row)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No logo here.")
    return row["logo_webp"]
