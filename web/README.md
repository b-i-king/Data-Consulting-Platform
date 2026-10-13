# Web frontend (Next.js)

The guest-facing website. See the root [README](../README.md) and
[docs/CODE_MAP.md](../docs/CODE_MAP.md) for how it fits with the API.

```bash
npm install
cp .env.example .env.local     # set NEXT_PUBLIC_API_URL
npm run dev                    # http://localhost:3000
npm run typecheck && npm run build
```

| Path | Purpose |
|---|---|
| `app/page.tsx` | Landing page |
| `app/start/page.tsx` | Guest wizard page |
| `components/wizard/Wizard.tsx` | Step order and all wizard state |
| `components/wizard/Step*.tsx` | One file per step |
| `components/wizard/ui.tsx` | Shared buttons, cards, progress bar |
| `lib/api.ts` | Every call to the FastAPI backend |
| `lib/types.ts` | Shapes of API responses |
| `lib/session.ts` | Login token for the current browser tab |
