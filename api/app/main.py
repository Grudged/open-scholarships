"""Open Scholarships — a free, open (CC BY 4.0) read-only API over the version-controlled
dataset. No auth, CORS open: any site may read it. The data is the product; this is the tap.

This service is intentionally read-only. Curation/approval (the write side) is owned by Mission
Control, which edits the canonical JSON files directly; this API auto-reloads on file change."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .loader import get_records, query


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warms the cache and aborts boot on any invalid/duplicate record (fail-fast).
    get_records()
    app.state.booted_at = datetime.now(timezone.utc).isoformat()
    yield


app = FastAPI(
    title="Open Scholarships API",
    version=config.DATASET_VERSION,
    description=(
        "Free, open (CC BY 4.0) directory of scholarships and student aid — Nevada first. "
        "Facts are gathered from primary/official sources. Attribution required; see /meta."
    ),
    lifespan=lifespan,
)

# Public good: allow any browser origin to read it.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


@app.middleware("http")
async def license_headers(request, call_next):
    resp = await call_next(request)
    resp.headers["X-License"] = config.DATA_LICENSE
    resp.headers["X-Attribution"] = config.ATTRIBUTION
    return resp


def _public(rec: dict) -> dict:
    """Strip internal-only keys (anything prefixed with '_') before serving."""
    return {k: v for k, v in rec.items() if not k.startswith("_")}


def _count_by(records: list[dict], key: str) -> dict:
    out: dict = {}
    for r in records:
        out[r.get(key)] = out.get(r.get(key), 0) + 1
    return out


@app.get("/healthz")
def healthz():
    return {"status": "ok", "count": len(get_records()), "booted_at": app.state.booted_at}


@app.get("/meta")
def meta():
    recs = get_records()
    return {
        "name": "Open Scholarships",
        "version": config.DATASET_VERSION,
        "license": config.DATA_LICENSE,
        "license_url": config.DATA_LICENSE_URL,
        "attribution_required": config.ATTRIBUTION,
        "source_repo": config.SOURCE_REPO,
        "count": len(recs),
        "by_status": _count_by(recs, "status"),
        "states": sorted({(r.get("geo") or {}).get("state") for r in recs if (r.get("geo") or {}).get("state")}),
        "booted_at": app.state.booted_at,
    }


@app.get("/scholarships")
def list_scholarships(
    state: str | None = None,
    level: str | None = Query(None, description="education_level value, e.g. high-school-senior"),
    field: str | None = Query(None, description="field of study; records open to any field always match"),
    basis: str | None = None,
    type: str | None = None,
    sponsor_type: str | None = None,
    deadline_after: str | None = Query(None, description="ISO date; undated (rolling) records always match"),
    amount_min: float | None = None,
    q: str | None = Query(None, description="free-text over name/summary/sponsor"),
    status: str | None = Query("active", description="'active' (default), 'needs-review', 'expired', 'archived', or 'any'"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    results = query(
        get_records(),
        state=state, level=level, field=field, basis=basis, type_=type,
        sponsor_type=sponsor_type, deadline_after=deadline_after, amount_min=amount_min,
        q=q, status=(None if status == "any" else status),
    )
    page = results[offset: offset + limit]
    return {
        "total": len(results),
        "limit": limit,
        "offset": offset,
        "license": config.DATA_LICENSE,
        "attribution": config.ATTRIBUTION,
        "results": [_public(r) for r in page],
    }


@app.get("/scholarships/{scholarship_id}")
def get_scholarship(scholarship_id: str):
    for r in get_records():
        if r["id"] == scholarship_id:
            return _public(r)
    raise HTTPException(status_code=404, detail="scholarship not found")
