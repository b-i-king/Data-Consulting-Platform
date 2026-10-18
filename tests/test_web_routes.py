"""
The website's addresses (web/lib/routes.ts) and the places that depend on them.

The backend sends people back to the website after Stripe (api/services/billing.py)
and after a QuickBooks / Xero / Supabase sign-in (api/services/oauth.py). If a page
is renamed in the website but not here (or the reverse), people land on a 404.
These checks read the website's source files; they don't need Node.js.
"""

import re
from pathlib import Path

from api.services import oauth

WEB = Path(__file__).resolve().parents[1] / "web"
ROUTES = (WEB / "lib" / "routes.ts").read_text(encoding="utf-8")


def _slugs(block_name: str) -> set[str]:
    """The address pieces in one of routes.ts's slug maps (e.g. WIZARD_STEP_SLUGS)."""
    block = ROUTES.split(f"export const {block_name} = {{", 1)[1].split("} as const", 1)[0]
    return set(re.findall(r':\s*"([a-z-]+)"', block))


def test_pages_exist_for_the_main_addresses():
    """/workspace and /get-started answer every address below them; /signup has its own page."""
    assert (WEB / "app" / "workspace" / "[[...view]]" / "page.tsx").exists()
    assert (WEB / "app" / "get-started" / "[[...step]]" / "page.tsx").exists()
    assert (WEB / "app" / "signup" / "page.tsx").exists()
    assert (WEB / "app" / "not-found.tsx").exists()
    # The old folders are gone (the redirects below take over).
    assert not (WEB / "app" / "app").exists() and not (WEB / "app" / "start").exists()


def test_oauth_return_pages_are_website_addresses():
    """After a provider's sign-in, the browser lands on a real workspace / Get Started page."""
    assert oauth.RETURN_PAGES["app"] == "/workspace/settings/integrations"
    assert oauth.RETURN_PAGES["start"] == "/get-started/connect"
    assert "integrations" in _slugs("SETTINGS_SLUGS")
    assert "connect" in _slugs("WIZARD_STEP_SLUGS")


def test_every_wizard_step_has_an_address():
    """Each StepId in Wizard.tsx has a slug in routes.ts, and the slugs are all different."""
    wizard = (WEB / "components" / "wizard" / "Wizard.tsx").read_text(encoding="utf-8")
    step_ids = set(re.findall(r'"(\w+)"', wizard.split("type StepId =", 1)[1].split(";", 1)[0]))
    block = ROUTES.split("export const WIZARD_STEP_SLUGS = {", 1)[1].split("} as const", 1)[0]
    assert set(re.findall(r"^\s*(\w+):", block, re.M)) == step_ids
    slugs = re.findall(r':\s*"([a-z-]+)"', block)
    assert len(slugs) == len(set(slugs))


def test_old_addresses_forward_to_the_new_ones():
    """Bookmarks, emails and Stripe links to /app or /start keep working."""
    config = (WEB / "next.config.mjs").read_text(encoding="utf-8")
    for old, new in [("/home", "/"), ("/app", "/workspace"), ("/start", "/get-started"), ("/login", "/signin")]:
        assert f'source: "{old}", destination: "{new}"' in config


def test_no_links_to_the_old_addresses():
    """The website's own links use the new addresses (lib/routes.ts), not /app or /start."""
    old = re.compile(r'["\'`](/app|/start)(["\'`?/])')
    for path in list((WEB / "app").rglob("*.ts*")) + list((WEB / "components").rglob("*.tsx")) + list((WEB / "lib").rglob("*.ts")):
        hits = old.findall(path.read_text(encoding="utf-8"))
        assert not hits, f"{path} still links to {hits}"
