"""Open Scholarships — free, open (CC BY 4.0) public API.

One FastAPI backs the whole public surface: the docs page (/), the full dataset bundle
(/scholarships.json), and the query API (/api/scholarships). It serves ONLY active (human-verified)
records — needs-review / archived never leave the curation side. Hosted on Hetzner behind Traefik.
Data lives in a git clone refreshed by cron; the loader's mtime cache reloads on change, so a
`git pull` publishes new approvals without a restart."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import config
from .loader import get_records, query

SITE_DIR = Path(os.getenv("OS_SITE_DIR", str(config.REPO_ROOT / "site")))


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_records()  # warm cache + fail-fast on any invalid/duplicate record
    app.state.booted_at = datetime.now(timezone.utc).isoformat()
    yield


app = FastAPI(
    title="Open Scholarships API",
    version=config.DATASET_VERSION,
    description="Free, open (CC BY 4.0) directory of scholarships and student aid. Verified records only.",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


@app.middleware("http")
async def license_headers(request, call_next):
    resp = await call_next(request)
    resp.headers["X-License"] = config.DATA_LICENSE
    resp.headers["X-Attribution"] = config.ATTRIBUTION
    return resp


def _availability(rec: dict) -> str:
    """Cycle availability, computed fresh from the deadline window vs today — never stored, so it
    can't go stale. open = accepting applications now; upcoming = opens on a future date;
    closed = this cycle's deadline has passed (annual/fixed awards still recur — they stay served,
    just aren't open right now); rolling = always open; unknown = no dates on record (see
    deadline.notes for any free-text cycle info)."""
    dl = rec.get("deadline") or {}
    today = date.today().isoformat()
    opens, closes = dl.get("opens"), dl.get("date")
    if dl.get("type") == "rolling":
        return "rolling"
    if opens and opens > today:
        return "upcoming"
    if closes and closes < today:
        return "closed"
    if (opens and opens <= today) or (closes and closes >= today):
        return "open"
    return "unknown"


def _public(rec: dict) -> dict:
    out = {k: v for k, v in rec.items() if not k.startswith("_")}
    out["availability"] = _availability(rec)  # computed, not stored — see _availability()
    return out


def _active() -> list[dict]:
    """The only records ever served publicly: status == active."""
    return [_public(r) for r in get_records() if r.get("status") == "active"]


def _meta() -> dict:
    active = _active()
    return {
        "name": "Open Scholarships",
        "version": config.DATASET_VERSION,
        "license": config.DATA_LICENSE,
        "license_url": config.DATA_LICENSE_URL,
        "attribution_required": config.ATTRIBUTION,
        "source_repo": config.SOURCE_REPO,
        "count": len(active),
        "states": sorted({(r.get("geo") or {}).get("state") for r in active if (r.get("geo") or {}).get("state")}),
        "booted_at": app.state.booted_at,
    }


@app.get("/healthz")
def healthz():
    return {"status": "ok", "count": len(_active()), "booted_at": app.state.booted_at}


@app.get("/meta")
@app.get("/meta.json")
def meta():
    return _meta()


@app.get("/scholarships.json")
def bundle():
    return {"meta": _meta(), "results": _active()}


@app.get("/scholarships")
@app.get("/api/scholarships")
def list_scholarships(
    state: str | None = None,
    level: str | None = Query(None, description="education_level value, e.g. high-school-senior"),
    field: str | None = Query(None, description="field of study; records open to any field always match"),
    basis: str | None = None,
    type: str | None = None,
    sponsor_type: str | None = None,
    availability: str | None = Query(None, description="filter by computed cycle status: open | upcoming | closed | rolling | unknown"),
    deadline_after: str | None = Query(None, description="ISO date; undated (rolling) records always match"),
    amount_min: float | None = None,
    q: str | None = Query(None, description="free-text over name/summary/sponsor"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    active = [r for r in get_records() if r.get("status") == "active"]
    results = query(
        active, state=state, level=level, field=field, basis=basis, type_=type,
        sponsor_type=sponsor_type, deadline_after=deadline_after, amount_min=amount_min,
        q=q, status=None,
    )
    if availability:
        results = [r for r in results if _availability(r) == availability]
    page = results[offset: offset + limit]
    return {
        "total": len(results), "limit": limit, "offset": offset,
        "license": config.DATA_LICENSE, "attribution": config.ATTRIBUTION,
        "results": [_public(r) for r in page],
    }


@app.get("/scholarships/{scholarship_id}")
def get_scholarship(scholarship_id: str):
    for r in get_records():
        if r["id"] == scholarship_id and r.get("status") == "active":
            return _public(r)
    raise HTTPException(status_code=404, detail="scholarship not found")


@app.get("/")
def docs_page():
    index = SITE_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="docs page not found")
