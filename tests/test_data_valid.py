"""Every record must validate against the schema and have a unique id. This is the gate that keeps
bad data out of the commons — run it in CI on every PR.

    cd ~/repos/open-scholarships && api/venv/bin/python -m pytest tests -q
"""
import json
from pathlib import Path

from jsonschema import Draft202012Validator

REPO = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((REPO / "schema" / "scholarship.schema.json").read_text())
RECORDS = sorted((REPO / "data").rglob("*.json"))


def test_records_exist():
    assert RECORDS, "no data records found under data/"


def test_each_record_valid():
    validator = Draft202012Validator(SCHEMA)
    for path in RECORDS:
        rec = json.loads(path.read_text())
        errors = sorted(validator.iter_errors(rec), key=lambda e: list(e.path))
        assert not errors, f"{path.name}: " + "; ".join(
            f"{list(e.path)}: {e.message}" for e in errors)


def test_unique_ids():
    ids = [json.loads(p.read_text())["id"] for p in RECORDS]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate ids: {dupes}"
