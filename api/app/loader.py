"""Load + validate the dataset. The JSON files under data/ are the source of truth; this turns
them into an in-memory list (Nevada-scale is tiny) and refuses to load anything that fails the
schema or duplicates an id — bad data must never silently ship to consumers.

get_records() caches with a (file-count, max-mtime) signature so an external edit — e.g. Mission
Control approving a record — is picked up automatically without restarting the service."""
from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from .config import DATA_DIR, SCHEMA_PATH


class DataError(Exception):
    """Raised when a record is malformed, invalid, or a duplicate — fail loud at boot."""


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def load_records(validate: bool = True) -> list[dict]:
    schema = _load_schema()
    validator = Draft202012Validator(schema)
    records: list[dict] = []
    seen: dict[str, str] = {}
    for path in sorted(DATA_DIR.rglob("*.json")):
        rel = str(path.relative_to(DATA_DIR))
        try:
            rec = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise DataError(f"{rel}: invalid JSON ({e})") from e
        if validate:
            errors = sorted(validator.iter_errors(rec), key=lambda e: list(e.path))
            if errors:
                msg = "; ".join(f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in errors[:5])
                raise DataError(f"{rel}: schema validation failed — {msg}")
        rid = rec["id"]
        if rid in seen:
            raise DataError(f"duplicate id '{rid}' in {rel} and {seen[rid]}")
        seen[rid] = rel
        rec["_source_file"] = rel  # internal-only; stripped before serving
        records.append(rec)
    return records


_cache: dict = {"records": None, "sig": None}


def _signature() -> tuple[int, float]:
    """(file count, newest mtime) over the data dir + schema — changes on any add/edit/delete."""
    files = list(DATA_DIR.rglob("*.json"))
    newest = SCHEMA_PATH.stat().st_mtime if SCHEMA_PATH.exists() else 0.0
    for p in files:
        newest = max(newest, p.stat().st_mtime)
    return (len(files), newest)


def get_records() -> list[dict]:
    sig = _signature()
    if _cache["records"] is None or sig != _cache["sig"]:
        _cache["records"] = load_records(validate=True)
        _cache["sig"] = sig
    return _cache["records"]


def _matches(rec: dict, *, state, level, field, basis, type_, sponsor_type,
             deadline_after, amount_min, q, status) -> bool:
    if status and rec.get("status") != status:
        return False
    geo = rec.get("geo") or {}
    elig = rec.get("eligibility") or {}
    levels = elig.get("education_level") or []
    if state:
        s = state.upper()
        residency = [r.upper() for r in elig.get("residency", [])]
        # National scholarships are open to every state, so they always pass a state filter.
        national = (geo.get("scope") == "national") or ("US" in residency)
        if not national and (geo.get("state") or "").upper() != s and s not in residency:
            return False
    if level and level not in levels and "any" not in levels:
        return False
    if field:
        fields = [f.lower() for f in (elig.get("fields_of_study") or [])]
        if fields and field.lower() not in fields:  # empty fields_of_study = open to any field
            return False
    if basis and (rec.get("award") or {}).get("basis") != basis:
        return False
    if type_ and rec.get("type") != type_:
        return False
    if sponsor_type and rec.get("sponsor_type") != sponsor_type:
        return False
    if amount_min is not None:
        amax = (rec.get("award") or {}).get("amount_max")
        if amax is None or amax < amount_min:
            return False
    if deadline_after:
        d = (rec.get("deadline") or {}).get("date")
        # Only date-bearing records are filtered; rolling/varies (no date) always pass so a date
        # filter never hides an always-open scholarship.
        if d and d < deadline_after:
            return False
    if q:
        hay = " ".join([rec.get("name", ""), rec.get("summary", ""), rec.get("sponsor", "")]).lower()
        if q.lower() not in hay:
            return False
    return True


def query(records: list[dict], **filters) -> list[dict]:
    return [r for r in records if _matches(r, **filters)]
