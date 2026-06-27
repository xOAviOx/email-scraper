"""ORM models: a User with a resettable daily quota, and a ScrapeJob.

The job queue is the `jobs` table itself — the worker polls for rows in the
'queued' state. That keeps the whole thing runnable on Windows with no Redis
or Celery; swap in RQ/Celery later if you outgrow DB polling.
"""

import os
import secrets
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

# Leads a user may collect per day. Override with the DAILY_QUOTA env var.
DEFAULT_DAILY_QUOTA = int(os.environ.get("DAILY_QUOTA", "400"))


def _new_token() -> str:
    return secrets.token_hex(24)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    api_token: Mapped[str] = mapped_column(String(64), unique=True, index=True,
                                           default=_new_token)
    daily_quota: Mapped[int] = mapped_column(Integer, default=DEFAULT_DAILY_QUOTA)
    quota_used: Mapped[int] = mapped_column(Integer, default=0)
    quota_date: Mapped[date] = mapped_column(Date, default=date.today)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    jobs: Mapped[list["ScrapeJob"]] = relationship(back_populates="user")

    def remaining_quota(self) -> int:
        """Leads still allowed today. The counter rolls over at midnight; we
        treat any quota_used from a previous day as already reset."""
        if self.quota_date != date.today():
            return self.daily_quota
        return max(0, self.daily_quota - self.quota_used)

    def spend_quota(self, n: int) -> None:
        """Record `n` leads against today's allowance, resetting first if the
        last spend was on an earlier day."""
        today = date.today()
        if self.quota_date != today:
            self.quota_date = today
            self.quota_used = 0
        self.quota_used += n


class ScrapeJob(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    # Inputs (comma-separated so the form maps straight onto run_scrape()).
    categories: Mapped[str] = mapped_column(Text)
    locations: Mapped[str] = mapped_column(Text)
    limit_per_query: Mapped[int] = mapped_column(Integer, default=20)
    max_leads: Mapped[int] = mapped_column(Integer, default=DEFAULT_DAILY_QUOTA)

    # Lifecycle: queued -> running -> done | failed
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    phase: Mapped[str] = mapped_column(String(20), default="")  # maps | emails
    progress_done: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)

    result_count: Mapped[int] = mapped_column(Integer, default=0)
    email_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="jobs")

    @property
    def percent(self) -> int:
        if self.status == "done":
            return 100
        if self.progress_total:
            return int(self.progress_done / self.progress_total * 100)
        return 0
