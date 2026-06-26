"""Token-authed API for the browser extension ("Collector").

The web app's own worker can run jobs server-side, but every request then comes
from one server IP — which Google Maps quickly rate-limits or blocks. The
extension flips that around: each user's browser claims their queued jobs and
scrapes from their own IP/session, then posts the leads back here.

Auth is the per-user `api_token` (already on the User model), sent as
`Authorization: Bearer <token>`. The lead CSV written here is byte-for-byte the
same format the worker writes, so downloads/exports keep working unchanged.
"""

import csv
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import models
from .db import SessionLocal

router = APIRouter(prefix="/api", tags=["extension"])

DATA_DIR = Path("data/jobs")
CSV_FIELDS = ["category", "location", "name", "website", "phone", "address",
              "rating", "emails"]


# --- dependencies ----------------------------------------------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_api_user(
    authorization: str = Header(default=""),
    x_api_token: str = Header(default=""),
    db: Session = Depends(get_db),
) -> models.User:
    """Resolve the user from a Bearer token (or X-API-Token fallback)."""
    token = ""
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_api_token:
        token = x_api_token.strip()
    user = (db.query(models.User).filter_by(api_token=token).first()
            if token else None)
    if user is None:
        raise HTTPException(401, "Invalid or missing API token")
    return user


def _owned_running_job(job_id: int, user: models.User, db: Session) -> models.ScrapeJob:
    job = db.get(models.ScrapeJob, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(404, "Job not found")
    return job


# --- lead persistence (same CSV the downloader reads) ----------------------
def _job_csv(job_id: int) -> Path:
    d = DATA_DIR / str(job_id)
    d.mkdir(parents=True, exist_ok=True)
    return d / "leads.csv"


def _dedupe_key(row: dict) -> str:
    """Mirror maps_email_scraper._dedupe_key so re-posting can't create dupes.
    (Reimplemented here to avoid importing the Playwright-heavy scraper module
    into the web process.)"""
    site = row.get("website", "") or ""
    if site:
        parts = urlsplit(site if site.startswith("http") else "https://" + site)
        host = parts.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return "site:" + host + parts.path.rstrip("/")
    phone_digits = re.sub(r"\D", "", row.get("phone", "") or "")
    return f"namephone:{(row.get('name', '') or '').lower()}|{phone_digits}"


def _normalize_emails(emails: list[str]) -> list[str]:
    """Lowercase + dedupe (order-preserving). The extension already runs the
    full junk filter client-side; this is a cheap last line of defense so the
    stored CSV never holds case-duplicates regardless of who posts."""
    seen: list[str] = []
    for e in emails or []:
        norm = (e or "").strip().lower()
        if norm and norm not in seen:
            seen.append(norm)
    return seen


def _existing_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    if path.exists():
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                keys.add(_dedupe_key(row))
    return keys


def _append_leads(job_id: int, leads: list["LeadIn"]) -> int:
    """Append new leads to the job's CSV, skipping ones already written.
    Returns the number actually written."""
    path = _job_csv(job_id)
    keys = _existing_keys(path)
    new_file = not path.exists() or path.stat().st_size == 0
    written = 0
    with open(path, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new_file:
            writer.writeheader()
        for lead in leads:
            row = {
                "category": lead.category or "",
                "location": lead.location or "",
                "name": lead.name or "",
                "website": lead.website or "",
                "phone": lead.phone or "",
                "address": lead.address or "",
                "rating": lead.rating or "",
                "emails": ";".join(lead.emails or []),
            }
            key = _dedupe_key(row)
            if key in keys:
                continue
            keys.add(key)
            writer.writerow(row)
            written += 1
    return written


# --- request bodies --------------------------------------------------------
class LeadIn(BaseModel):
    name: str = ""
    website: str = ""
    phone: str = ""
    address: str = ""
    rating: str = ""
    category: str = ""
    location: str = ""
    emails: list[str] = Field(default_factory=list)


class LeadsBody(BaseModel):
    leads: list[LeadIn] = Field(default_factory=list)


# --- endpoints -------------------------------------------------------------
@router.get("/me")
def me(user: models.User = Depends(current_api_user)):
    """Validate the token and report quota — the extension shows this on connect."""
    return {
        "email": user.email,
        "daily_quota": user.daily_quota,
        "remaining_quota": user.remaining_quota(),
    }


@router.post("/jobs/claim")
def claim_job(user: models.User = Depends(current_api_user),
              db: Session = Depends(get_db)):
    """Atomically take this user's oldest queued job and mark it running.
    Returns {"job": null} when there's nothing to do."""
    job = (db.query(models.ScrapeJob)
             .filter(models.ScrapeJob.user_id == user.id,
                     models.ScrapeJob.status == "queued")
             .order_by(models.ScrapeJob.id)
             .first())
    if job is None:
        return {"job": None}
    job.status = "running"
    job.phase = "maps"
    db.commit()
    return {"job": {
        "id": job.id,
        "categories": job.categories,
        "locations": job.locations,
        "limit_per_query": job.limit_per_query,
        "max_leads": job.max_leads,
    }}


@router.post("/jobs/{job_id}/progress")
def report_progress(job_id: int, payload: dict = Body(...),
                    user: models.User = Depends(current_api_user),
                    db: Session = Depends(get_db)):
    job = _owned_running_job(job_id, user, db)
    if "phase" in payload:
        job.phase = str(payload["phase"])[:20]
    if "done" in payload:
        job.progress_done = int(payload["done"])
    if "total" in payload:
        job.progress_total = int(payload["total"])
    db.commit()
    return {"ok": True}


@router.post("/jobs/{job_id}/leads")
def post_leads(job_id: int, body: LeadsBody,
               user: models.User = Depends(current_api_user),
               db: Session = Depends(get_db)):
    job = _owned_running_job(job_id, user, db)
    written = _append_leads(job.id, body.leads)
    return {"written": written}


@router.post("/jobs/{job_id}/complete")
def complete_job(job_id: int, payload: dict = Body(default={}),
                 user: models.User = Depends(current_api_user),
                 db: Session = Depends(get_db)):
    job = _owned_running_job(job_id, user, db)

    # Guarantee a results file exists even if zero leads came back, so the
    # download endpoint doesn't 404.
    csv_path = _job_csv(job.id)
    if not csv_path.exists():
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()

    job.result_count = int(payload.get("result_count", job.result_count) or 0)
    job.email_count = int(payload.get("email_count", job.email_count) or 0)
    job.status = "done"
    job.finished_at = datetime.utcnow()

    # Quota is spent on what was actually delivered (matches the worker).
    user.spend_quota(job.result_count)
    db.commit()
    return {"ok": True}


@router.post("/jobs/{job_id}/fail")
def fail_job(job_id: int, payload: dict = Body(default={}),
             user: models.User = Depends(current_api_user),
             db: Session = Depends(get_db)):
    job = _owned_running_job(job_id, user, db)
    job.status = "failed"
    job.error = str(payload.get("error", "Reported failed by collector"))[:4000]
    job.finished_at = datetime.utcnow()
    db.commit()
    return {"ok": True}
