# Security

How the platform protects clients' data, layer by layer, and what is still
to do. Settings are in `.env.example`; deployment steps in
[DEPLOYMENT.md](DEPLOYMENT.md).

## Signing in

| Protection | What it does | Code |
|---|---|---|
| Passwords | Supabase Auth keeps them (a one-way scrypt hash in local mode). 8+ characters. | `api/services/auth.py` |
| Lockout | 3 wrong passwords (or two-step codes) in a row lock sign-in for 15 minutes; each try says how many are left; a password reset unlocks at once. Unknown emails get the same replies. | `api/services/login_guard.py` |
| Per-IP limits | 10 sign-in / sign-up / password requests a minute per IP address, 300 requests a minute overall (429 + Retry-After). Webhooks and `/health` are never limited. | `api/core/rate_limit.py` |
| Signed-in devices | At most **3 devices** per person; a 4th signs out the one used longest ago. Signed out after **8 idle hours**. Settings → Your devices: sign out one, or all others. A password reset signs out **every** device; an admin can too. Each browser has a random device id so its tabs count once. Only "Chrome on Windows"-style labels are stored — no IP addresses. | `api/services/sessions.py`, `dim.sessions` |
| Two-step sign-in | A 6-digit authenticator-app code after the password (standard TOTP, RFC 6238), plus 10 one-time backup codes (only hashes kept). The key is stored encrypted. **Required for admins and consultants**: until it's on, the Admin panel, consultant pages and every client's workspace refuse them. Anyone else may turn it on. An admin can reset it (lost phone and codes). | `api/services/mfa.py` |

## Access inside the app

- A client sees only their own account (and their team's plan); a consultant
  only the clients assigned to them; deactivated consultants none
  (`get_acting_context`, `api/services/consultants.py`).
- Team roles: the **owner** does everything, a **developer** changes data but
  not billing or people, a **member** only views — enforced on every change
  (`api/services/teams.py`).

## The database

| Protection | What it does |
|---|---|
| Least-privilege login | The API signs in as `dcp_api` (role `dcp_app`): it can read and write rows but can't create, change or drop tables, can't read Supabase's own schemas, and **can't skip Row Level Security**. The owner login (`ADMIN_DATABASE_URL`) is used only by `scripts/setup_database.py` and one-off scripts. |
| Row Level Security | Client data (`fact.account_data`, `fact.entities`, `fact.entity_sources`) is visible only for the account named in each transaction (`set_config('app.account_id', ...)`); with no account named, nothing. So the **database** enforces "one client at a time", not only the API. `dim` tables are closed to Supabase's browser roles. |
| Encrypted fields | **Emails and phone numbers** in stored client data are encrypted (Fernet: AES-128 + HMAC) before they're written, and decrypted only for that client's own reads and downloads (`api/services/pii.py`, `api/core/crypto.py`). **Names stay readable** for reports ("top customers") and matching. Saved logins to clients' tools and two-step keys are encrypted too. The key is `DATA_ENCRYPTION_KEY` — keep it safe; losing it makes that data unreadable. |
| At rest and in transit | Supabase encrypts the disk (AES-256) and every connection uses TLS. |
| HIPAA approval | Picking "Health Care" (NAICS 62) at sign-up — or asking in Settings → Health information — opens a **request an admin must confirm** (Admin → HIPAA requests): the practice's legal name, its **NPI** (10 digits, checked with the official check digit; the admin opens it in the public NPPES registry) and what it would store. **While it's waiting, storing data is paused** (loads and syncs refuse); cleaning and analyzing in memory still work. Approved → the audit log turns on. Denied → ordinary business data only. The Terms forbid patient health information without approval and a BAA (`api/services/hipaa.py`). |
| Audit log (healthcare) | For **HIPAA-approved clients only** (`dim.accounts.is_healthcare`, set by an admin's approval): every sign-in, view, export, load, delete, download, connector action and AI question goes to `dim.audit_log` — who, their role, when, and names / counts, never the data. The owner sees it in Settings → Activity log (CSV download). Other clients write nothing. |
| No dangerous files | Pickle and HDF5 uploads are refused (they can run code); files are read only through `api/services/file_reader.py`. |

## The website

- Security headers (Content-Security-Policy and friends) in `web/next.config.mjs`.
- The login token lives in `sessionStorage` (cleared when the tab closes), and
  is useless once its device session ends.
- Only the website's own address may call the API (CORS, `ALLOWED_ORIGINS`).

## Monitoring and libraries

- **Beacon (Gemini)** sees column names, number / date ranges and repeating
  category values — never rows, never values that tell people apart, and never
  any value of an email or phone column (`api/services/ai_assistant.py`).
- **Error monitoring** (Sentry, when `SENTRY_DSN` is set): no request bodies,
  cookies, IP addresses, query strings, sign-in headers or local variables;
  emails and phone numbers in error messages become `[email]` / `[phone]`
  (`api/core/monitoring.py`).
- **Known vulnerabilities**: `.github/workflows/security_checks.yml` runs
  `pip-audit` and `npm audit` on every pull request and every Monday;
  `.github/dependabot.yml` opens pull requests for fixed versions.

## Healthcare (HIPAA): before storing patient data

You don't need HIPAA for business numbers (revenue by service, visit counts,
staffing) or customer names: HIPAA covers patients' **health** information.
The approval step above makes sure a business that wants to store that is a
real healthcare business before anything goes into the database.

Everything above helps, but HIPAA also needs **Business Associate Agreements
(BAAs)** with every service that touches patient information — Supabase
(higher plans + HIPAA add-on), the API host (Render or another host that
signs one), and Google if Beacon's Gemini sees the data (its free key can't).
Confirm current terms with each provider, and talk to a HIPAA consultant or
attorney before the first healthcare client. Until then, work with business
data that has no patient information (revenue by service, visit counts).
The audit log is deleted with the account; if a BAA asks you to keep logs
(often 6 years), export them first.

## Still to do

- Rotate `DATA_ENCRYPTION_KEY` safely (re-encrypt with a new key).
- Sentry for the website too (only the API reports today).
- A BAA-ready hosting setup when the first healthcare client signs.
