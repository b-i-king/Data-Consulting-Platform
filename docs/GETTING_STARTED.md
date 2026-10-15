# Getting started (local development)

You need **Python 3.12+** and **Node.js 20+**.

## 1. Backend (FastAPI)

```bash
# from the project root
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # optional: defaults work without it
uvicorn api.main:app --reload        # http://localhost:8000
```

- Interactive API docs: http://localhost:8000/docs (try every endpoint in the browser).
- Health check: http://localhost:8000/health → `"database": "in-memory"` means no
  `DATABASE_URL` is set, which is fine for development.

## 2. Frontend (Next.js)

```bash
cd web
npm install
cp .env.example .env.local
npm run dev                          # http://localhost:3000
```

Open http://localhost:3000 → **Start as a guest**.

## 3. Run the tests

```bash
pytest -q                            # backend: services + full API flow
cd web && npm run lint               # frontend: ESLint
cd web && npm run typecheck          # frontend: TypeScript checks
cd web && npm run build              # frontend: production build
```

## 4. (Optional) Connect Supabase locally

1. Create a project at https://supabase.com.
2. **Connect** → **Transaction pooler** (port 6543). Put it in `.env` as `DATABASE_URL=...`.
3. Create the tables: `python scripts/setup_database.py` (runs `db/schema.sql` and
   `db/warehouse.sql`, then lists every table) — or paste both files into the SQL Editor.
4. Optional, for Supabase passwords: `SUPABASE_URL` and `SUPABASE_KEY` (anon /
   publishable key). Without them, passwords are hashed by the API itself.
5. Restart the API; `/health` should now say `"database": "connected"` (and
   `"auth": "supabase"` with step 4).

The full first run as the consultant: [RUN_THROUGH.md](RUN_THROUGH.md).

## Sample files to try

Create these to see multi-file categorization:

`sales_2023.csv`
```
order_id,customer_id,order_date,amount
1,10,2023-01-05,25.00
1,10,2023-01-05,25.00
2,11,2023-03-12,
```

`sales_2024.csv`
```
order_id,customer_id,order_date,amount
4,10,2024-02-01,30.00
```

`customers.csv`
```
customer_id,first_name,last_name,email
10,Ana,Diaz,ana@example.com
11,Ben,Lee,
```

Expected: the two sales files are grouped as **Transactions (partitioned by
year)**, customers stand alone, and they link on `customer_id`.
