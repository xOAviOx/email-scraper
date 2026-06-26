"""Throwaway smoke test for the extension API lifecycle. Run, then delete.

    set DATABASE_URL=sqlite:///smoke_test.db   (Windows: $env:DATABASE_URL=...)
    python _smoke_ext_api.py
"""
import csv
from pathlib import Path

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.db import SessionLocal
from webapp import models

client = TestClient(app)

# Register a user via the web form, then read their api_token from the DB.
client.post("/register", data={"email": "ext@test.com", "password": "secret123"})
db = SessionLocal()
u = db.query(models.User).filter_by(email="ext@test.com").first()
tok = u.api_token
start_remaining = u.remaining_quota()
db.close()
H = {"Authorization": f"Bearer {tok}"}

# /api/me
me = client.get("/api/me", headers=H).json()
assert me["email"] == "ext@test.com", me
print("me:", me)

# Bad token -> 401
assert client.get("/api/me", headers={"Authorization": "Bearer nope"}).status_code == 401

# Queue a job (web form) then claim it via the API.
client.post("/jobs", data={"categories": "dentists", "locations": "Pune",
                           "limit_per_query": "10"})
claim = client.post("/api/jobs/claim", headers=H).json()
job = claim["job"]
assert job and job["categories"] == "dentists", claim
jid = job["id"]
print("claimed job:", job)

# Claiming again -> nothing left queued.
assert client.post("/api/jobs/claim", headers=H).json()["job"] is None

# Progress + leads (with a dupe to prove server-side dedupe).
client.post(f"/api/jobs/{jid}/progress", headers=H,
            json={"phase": "maps", "done": 1, "total": 1})
leads = [
    {"category": "dentists", "location": "Pune", "name": "Bright Smiles",
     "website": "https://bright.example", "phone": "020 111", "address": "MG Rd",
     "rating": "4.5", "emails": ["info@bright.example", "INFO@bright.example"]},
    {"category": "dentists", "location": "Pune", "name": "Bright Smiles",
     "website": "https://bright.example", "phone": "020 111", "address": "MG Rd",
     "rating": "4.5", "emails": ["info@bright.example"]},
    {"category": "dentists", "location": "Pune", "name": "No Site", "website": "",
     "phone": "020 222", "address": "FC Rd", "rating": "4.0", "emails": []},
]
w = client.post(f"/api/jobs/{jid}/leads", headers=H, json={"leads": leads}).json()
print("written:", w)
assert w["written"] == 2, w  # the dupe website collapsed

# Complete -> spends quota = result_count.
client.post(f"/api/jobs/{jid}/complete", headers=H,
            json={"result_count": 2, "email_count": 1})

# Verify CSV written in the exact format the downloader reads.
p = Path("data/jobs") / str(jid) / "leads.csv"
rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
print("csv rows:", len(rows), "| emails cell:", repr(rows[0]["emails"]))
assert len(rows) == 2 and rows[0]["emails"] == "info@bright.example"

# Verify the existing download endpoint serves the file (logged-in session).
client.post("/login", data={"email": "ext@test.com", "password": "secret123"})
dl = client.get(f"/jobs/{jid}/download?format=csv")
assert dl.status_code == 200 and b"Bright Smiles" in dl.content, dl.status_code

# Verify quota spent.
db = SessionLocal()
u = db.query(models.User).filter_by(email="ext@test.com").first()
print("quota: start", start_remaining, "-> remaining", u.remaining_quota())
assert u.remaining_quota() == start_remaining - 2
db.close()

print("\nSMOKE TEST PASSED")
