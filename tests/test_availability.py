"""`availability` is the field a student acts on, so it gets its own gate.

The rule these lock in: **`open` requires a close date still in the future.** A past `opens` with
no close date is `unknown`. Before this was enforced, 13 of the 18 records the API reported as
`open` rested on nothing but a stale `opens` — one of them 721 days old — so the API was telling
students to apply to windows that had shut two cycles earlier.

    api/venv/bin/python -m pytest tests -q
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "api"))

# Import the rule directly, NOT through app.main — main.py imports FastAPI, and validate.yml is a
# lean data gate that installs only jsonschema + pytest. Importing the app here is what turned CI
# red on the first attempt.
from app.availability import availability as _availability  # noqa: E402

TODAY = date.today()
PAST = (TODAY - timedelta(days=400)).isoformat()
RECENT_PAST = (TODAY - timedelta(days=20)).isoformat()
FUTURE = (TODAY + timedelta(days=60)).isoformat()
FAR_FUTURE = (TODAY + timedelta(days=300)).isoformat()


def av(**deadline):
    return _availability({"deadline": deadline})


# --- the regression this file exists for --------------------------------

def test_past_opens_without_close_is_unknown_not_open():
    """The bug: a window we know started but cannot confirm is still accepting."""
    assert av(opens=PAST) == "unknown"
    assert av(opens=RECENT_PAST) == "unknown"


def test_past_opens_with_future_close_is_open():
    """A real window with both ends — the only case that earns 'open'."""
    assert av(opens=PAST, date=FUTURE) == "open"
    assert av(opens=RECENT_PAST, date=FAR_FUTURE) == "open"


def test_future_close_alone_is_open():
    assert av(date=FUTURE) == "open"


# --- the other states ---------------------------------------------------

def test_no_dates_is_unknown():
    assert av() == "unknown"
    assert _availability({}) == "unknown"
    assert _availability({"deadline": None}) == "unknown"


def test_future_opens_is_upcoming():
    assert av(opens=FUTURE) == "upcoming"
    assert av(opens=FUTURE, date=FAR_FUTURE) == "upcoming"


def test_past_close_is_closed():
    """Closed for this cycle — still served, because annual awards recur."""
    assert av(date=PAST) == "closed"
    assert av(opens=PAST, date=PAST) == "closed"


def test_rolling_wins_over_dates():
    assert av(type="rolling") == "rolling"
    assert av(type="rolling", opens=PAST, date=PAST) == "rolling"


def test_close_today_is_still_open():
    """Applications close at end of day — today is not too late."""
    assert av(date=TODAY.isoformat()) == "open"


def test_opens_today_without_close_is_unknown():
    """Even 'opened today' is not evidence it is still open without a close date."""
    assert av(opens=TODAY.isoformat()) == "unknown"


# --- the client-side mirror must not drift ------------------------------

def test_mission_control_mirror_matches():
    """Mission Control re-implements this in JS for the review card. If the two drift, the
    reviewer approves against a different reading than the public API serves."""
    mc = Path.home() / "repos" / "mission-control" / "app" / "templates" / "views" / "scholarships.html"
    if not mc.exists():
        return  # mission-control not checked out here; CI runs this repo alone
    src = mc.read_text()
    assert "if (c && c >= today) return 'open';" in src, (
        "Mission Control's availOf() no longer requires a future close date for 'open' — "
        "it has drifted from _availability() in api/app/main.py")
    assert "if ((o && o <= today) || (c && c >= today)) return 'open';" not in src, (
        "Mission Control's availOf() still carries the past-opens-means-open bug")
