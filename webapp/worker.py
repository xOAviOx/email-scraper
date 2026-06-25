"""Background worker: claims queued jobs and runs the scraper off-request.

Run it in its own terminal, from the repo root:

    python -m webapp.worker

It polls the jobs table, runs maps_email_scraper.run_scrape() for each queued
job, writes data/jobs/<id>/leads.csv, updates progress as it goes, and spends
the user's daily quota on completion.

This single-process poller is intentionally simple and Windows-friendly. For
horizontal scale, swap the claim loop for RQ/Celery + Redis — run_scrape()
stays exactly the same.
"""

import time
import traceback
from datetime import datetime
from pathlib import Path

import maps_email_scraper as scraper  # repo-root module

from . import models
from .db import SessionLocal, init_db

DATA_DIR = Path("data/jobs")
POLL_SECONDS = 3


def _job_csv(job_id: int) -> Path:
    d = DATA_DIR / str(job_id)
    d.mkdir(parents=True, exist_ok=True)
    return d / "leads.csv"


def run_job(job_id: int) -> None:
    """Execute one job end to end inside its own DB session."""
    db = SessionLocal()
    try:
        job = db.get(models.ScrapeJob, job_id)
        if job is None:
            return
        csv_path = _job_csv(job.id)

        def progress(phase, done, total, _msg):
            job.phase = phase
            job.progress_done = done
            job.progress_total = total
            db.commit()

        rows = scraper.run_scrape(
            categories=[c.strip() for c in job.categories.split(",") if c.strip()],
            locations=[l.strip() for l in job.locations.split(",") if l.strip()],
            limit=job.limit_per_query,
            max_leads=job.max_leads,
            output_path=str(csv_path),
            progress=progress,
        )

        job.result_count = len(rows)
        job.email_count = sum(1 for r in rows if r.get("emails"))
        job.status = "done"
        job.finished_at = datetime.utcnow()

        # Quota is spent on what we actually delivered, not what was requested.
        user = db.get(models.User, job.user_id)
        if user is not None:
            user.spend_quota(job.result_count)
        db.commit()
    except Exception as exc:  # noqa: BLE001 — record any failure on the job
        db.rollback()
        job = db.get(models.ScrapeJob, job_id)
        if job is not None:
            job.status = "failed"
            job.error = f"{exc}\n{traceback.format_exc()}"[:4000]
            job.finished_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()


def _claim_next() -> int | None:
    """Atomically move the oldest queued job to 'running' and return its id."""
    db = SessionLocal()
    try:
        job = (db.query(models.ScrapeJob)
                 .filter(models.ScrapeJob.status == "queued")
                 .order_by(models.ScrapeJob.id)
                 .first())
        if job is None:
            return None
        job.status = "running"
        db.commit()
        return job.id
    finally:
        db.close()


def main() -> None:
    init_db()
    print("worker up — polling for queued jobs (Ctrl+C to stop)")
    while True:
        job_id = _claim_next()
        if job_id is None:
            time.sleep(POLL_SECONDS)
            continue
        print(f"running job {job_id}…")
        run_job(job_id)
        print(f"job {job_id} finished")


if __name__ == "__main__":
    main()
