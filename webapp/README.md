# LeadHarvest — web front-end for the scraper

A small SaaS-shaped wrapper around `maps_email_scraper.py`. Users sign up, run a
scrape within a **daily lead quota** (default 400), watch progress, and download
results as **CSV / XLSX / JSON** when the job finishes.

## How it fits together

```
Browser ──submit──▶ FastAPI (webapp/main.py) ──row──▶ jobs table (queued)
                                                          │ polled
                          download ◀── data/jobs/<id>/leads.csv ◀── worker (webapp/worker.py)
                                                          │ runs
                                          maps_email_scraper.run_scrape()
```

The scraper is slow (real Chromium + politeness delays), so it never runs inside
a web request. The web process only enqueues jobs; the **worker** process runs
them and writes a per-job CSV that the download endpoint converts on demand.

## Run it locally (from the repo root)

```powershell
pip install -r requirements.txt
playwright install chromium

# Terminal 1 — the worker
python -m webapp.worker

# Terminal 2 — the web app
uvicorn webapp.main:app --reload
```

Open http://127.0.0.1:8000 , register, and start a scrape.

> Run both processes **from the repo root** so `import maps_email_scraper` and
> `import format_leads` resolve.

## Config (env vars)

| Var | Default | Notes |
|-----|---------|-------|
| `DATABASE_URL` | `sqlite:///webapp.db` | Set to your Postgres/Supabase URL in prod. |
| `SECRET_KEY` | `dev-insecure-change-me` | **Must** be set to a random value in prod (signs sessions). |

## What's deliberately left for phase 2 (the "real deployable SaaS" parts)

These are scoped out of this first slice — here's where each hooks in:

- **Proxy rotation / anti-block** — inside `scrape_maps()` / `extract_emails_from_site()`
  (per-request proxy + retry). Required before any public scale.
- **Billing & tiers** — `User.daily_quota` is already the lever; wire Stripe to bump it.
- **Compliance (DPDP / GDPR / CAN-SPAM)** — ToS-aware sourcing, an opt-out/suppression
  list, and terms that put contact responsibility on the user. Treat as a launch blocker,
  not a nice-to-have.
- **Email delivery of results** — the worker is the place to fire a "your leads are ready"
  email (you already have the Gmail integration available).
- **Distributed queue** — swap the DB poll in `worker.py` for RQ/Celery + Redis;
  `run_scrape()` is unchanged.

## API (token auth, for later)

Each user has an `api_token` column already. Exposing `POST /api/v1/jobs` +
`GET /api/v1/jobs/{id}` with `Authorization: Bearer <token>` is a thin addition
on top of the existing service functions.
