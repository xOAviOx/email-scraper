"""FastAPI app: accounts, quota-checked job submission, status, downloads.

Run from the repo root (so the scraper modules import cleanly):

    uvicorn webapp.main:app --reload

Set SECRET_KEY in production (sessions are signed with it). DATABASE_URL
controls the DB; it defaults to a local SQLite file.
"""

import os
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import api, exporters, models, security
from .db import SessionLocal, init_db

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="LeadHarvest")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SECRET_KEY", "dev-insecure-change-me"),
)

# The extension talks to /api/* from a chrome-extension:// origin. Auth there
# is a Bearer token (not cookies), so a permissive CORS policy is safe; lock it
# down with API_CORS_ORIGINS (comma-separated) in production if you prefer.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in
                   os.environ.get("API_CORS_ORIGINS", "*").split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Token-authed endpoints the browser extension uses to claim jobs and post leads.
app.include_router(api.router)


@app.on_event("startup")
def _startup() -> None:
    init_db()


# --- DB session per request -----------------------------------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_user(request: Request, db: Session) -> models.User | None:
    uid = request.session.get("uid")
    return db.get(models.User, uid) if uid else None


# --- Auth ------------------------------------------------------------------
@app.get("/register", response_class=HTMLResponse)
def register_form(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "error": None})


@app.post("/register")
def register(request: Request, email: str = Form(...), password: str = Form(...),
             db: Session = Depends(get_db)):
    email = email.strip().lower()
    if not email or len(password) < 6:
        return templates.TemplateResponse("register.html", {
            "request": request, "error": "Enter an email and a password of 6+ characters."})
    if db.query(models.User).filter_by(email=email).first():
        return templates.TemplateResponse("register.html", {
            "request": request, "error": "That email is already registered."})
    user = models.User(email=email, password_hash=security.hash_password(password))
    db.add(user)
    db.commit()
    request.session["uid"] = user.id
    return RedirectResponse("/", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...),
          db: Session = Depends(get_db)):
    user = db.query(models.User).filter_by(email=email.strip().lower()).first()
    if not user or not security.verify_password(password, user.password_hash):
        return templates.TemplateResponse("login.html", {
            "request": request, "error": "Wrong email or password."})
    request.session["uid"] = user.id
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# --- Dashboard + job submission -------------------------------------------
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    jobs = (db.query(models.ScrapeJob)
              .filter_by(user_id=user.id)
              .order_by(models.ScrapeJob.id.desc())
              .limit(50).all())
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "user": user, "jobs": jobs,
        "remaining": user.remaining_quota(), "formats": exporters.available_formats(),
        "error": request.query_params.get("error"),
    })


@app.post("/jobs")
def submit_job(request: Request, categories: str = Form(...), locations: str = Form(...),
               limit_per_query: int = Form(20), db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    remaining = user.remaining_quota()
    if remaining <= 0:
        return RedirectResponse(
            "/?error=Daily+limit+reached.+Come+back+tomorrow+for+more.",
            status_code=303)
    if not categories.strip() or not locations.strip():
        return RedirectResponse("/?error=Enter+at+least+one+category+and+location.",
                                status_code=303)

    job = models.ScrapeJob(
        user_id=user.id,
        categories=categories.strip(),
        locations=locations.strip(),
        limit_per_query=max(1, min(limit_per_query, 50)),
        max_leads=remaining,  # this run can't exceed what's left today
    )
    db.add(job)
    db.commit()
    return RedirectResponse("/", status_code=303)


# --- Job status (polled by the dashboard) + download ----------------------
def _owned_job(job_id: int, user: models.User, db: Session) -> models.ScrapeJob:
    job = db.get(models.ScrapeJob, job_id)
    if not job or job.user_id != user.id:
        raise HTTPException(404, "Job not found")
    return job


@app.get("/jobs/{job_id}/status")
def job_status(job_id: int, request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        raise HTTPException(401)
    job = _owned_job(job_id, user, db)
    return {
        "id": job.id, "status": job.status, "phase": job.phase,
        "percent": job.percent, "result_count": job.result_count,
        "email_count": job.email_count, "error": job.error,
    }


@app.get("/jobs/{job_id}/download")
def download(job_id: int, request: Request, format: str = "csv",
             db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    job = _owned_job(job_id, user, db)
    if job.status != "done":
        raise HTTPException(409, "Job is not finished yet")

    csv_path = Path("data/jobs") / str(job.id) / "leads.csv"
    if not csv_path.exists():
        raise HTTPException(404, "Results file is missing")
    try:
        data, media_type, ext = exporters.export(csv_path, format)
    except ValueError:
        raise HTTPException(400, "Unsupported format")
    return Response(content=data, media_type=media_type, headers={
        "Content-Disposition": f'attachment; filename="leads_job{job.id}.{ext}"'})
