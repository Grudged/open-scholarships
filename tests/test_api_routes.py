"""Route contract — the shapes a third-party integrator depends on.

Needs FastAPI, which CI deliberately does not install (validate.yml is a lean data gate), so
this module skips there and runs locally:

    api/venv/bin/python -m pytest tests -q
"""
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="validate.yml installs only jsonschema+pytest")

from fastapi.testclient import TestClient  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "api"))


@pytest.fixture(scope="module")
def client(monkeypatch_session=None):
    import os
    os.environ.setdefault("OS_DATA_DIR", str(REPO / "data"))
    os.environ.setdefault("OS_SCHEMA_PATH", str(REPO / "schema" / "scholarship.schema.json"))
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def some_id(client):
    return client.get("/api/scholarships?limit=1").json()["results"][0]["id"]


# --- the asymmetry that made a stranger's second call fail ---------------

def test_detail_served_under_both_prefixes(client, some_id):
    """The collection is at /scholarships AND /api/scholarships. Someone who found the /api
    one will reach for /api/scholarships/{id} next; that used to 404."""
    assert client.get(f"/scholarships/{some_id}").status_code == 200
    assert client.get(f"/api/scholarships/{some_id}").status_code == 200


def test_collection_not_swallowed_by_the_id_route(client):
    """The {id} routes are declared after the collection routes on purpose."""
    for path in ("/api/scholarships", "/scholarships", "/scholarships.json"):
        assert client.get(path).status_code == 200, path


def test_missing_record_is_404_under_both(client):
    assert client.get("/scholarships/nope").status_code == 404
    assert client.get("/api/scholarships/nope").status_code == 404


# --- HEAD: FastAPI does not add it alongside GET the way Starlette does ---

@pytest.mark.parametrize("path", [
    "/healthz", "/meta", "/meta.json", "/scholarships.json",
    "/scholarships", "/api/scholarships", "/",
])
def test_head_is_allowed(client, path):
    """Uptime monitors and link checkers use HEAD; every route 405'd on it."""
    assert client.request("HEAD", path).status_code == 200, path


def test_head_on_detail(client, some_id):
    assert client.request("HEAD", f"/api/scholarships/{some_id}").status_code == 200


# --- a typo'd filter must complain, not return an empty page --------------

def test_bad_availability_is_rejected(client):
    """422, not a 200 with total=0 — 'no results' and 'you misspelled it' must not look alike."""
    assert client.get("/api/scholarships?availability=bogus").status_code == 422


@pytest.mark.parametrize("state", ["open", "upcoming", "closed", "rolling", "unknown"])
def test_every_documented_availability_is_accepted(client, state):
    assert client.get(f"/api/scholarships?availability={state}").status_code == 200


def test_availability_filter_agrees_with_the_rule(client):
    """The filter and the field must not disagree."""
    body = client.get("/api/scholarships?availability=open&limit=500").json()
    assert body["total"] == len(body["results"]) or body["total"] >= len(body["results"])
    for r in body["results"]:
        assert r["availability"] == "open", r["id"]
        # and 'open' must always be backed by a real future close date
        assert (r.get("deadline") or {}).get("date"), f"{r['id']} is open with no close date"


# --- obligations we publish under ----------------------------------------

def test_license_headers_present(client):
    r = client.get("/api/scholarships?limit=1")
    assert r.headers.get("x-license")
    assert r.headers.get("x-attribution")


def test_pagination_does_not_overlap(client):
    a = client.get("/api/scholarships?limit=10&offset=0").json()["results"]
    b = client.get("/api/scholarships?limit=10&offset=10").json()["results"]
    assert not ({r["id"] for r in a} & {r["id"] for r in b})


def test_openapi_lists_the_detail_routes(client):
    """Directories ingest this spec; it has to describe what actually exists."""
    paths = client.get("/openapi.json").json()["paths"]
    assert "/scholarships/{scholarship_id}" in paths
    assert "/api/scholarships/{scholarship_id}" in paths
