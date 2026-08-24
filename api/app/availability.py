"""Cycle availability — the single rule that decides what a student is told about a scholarship.

Deliberately dependency-free (stdlib only, no FastAPI). It lives apart from main.py so CI can
test it without installing a web server: validate.yml is a lean data gate (`jsonschema pytest`),
and the rule that decides whether we say "apply now" has no business requiring uvicorn to verify.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

# The complete set, exported so the API can reject a typo'd filter instead of quietly
# returning zero results — which reads as "no scholarships" rather than "you misspelled it".
AvailabilityState = Literal["open", "upcoming", "closed", "rolling", "unknown"]
STATES: tuple[str, ...] = ("open", "upcoming", "closed", "rolling", "unknown")


def availability(rec: dict) -> str:
    """Computed fresh from the deadline window vs today — never stored, so it can't go stale.

    open     = accepting applications now
    upcoming = opens on a future date
    closed   = this cycle's deadline has passed (annual/fixed awards still recur — they stay
               served, they just aren't open right now)
    rolling  = always open
    unknown  = we don't have the dates to say (see deadline.notes for free-text cycle info)

    `open` REQUIRES a close date still in the future. A past `opens` with no close date is
    `unknown`, not `open` — we know the window started at some point and have no idea whether it
    is still accepting. Reading a bare past `opens` as "apply now" made the API tell students to
    apply to 13 of its 18 supposedly-open awards, several of whose windows had shut over a year
    earlier (one 721 days). An honest `unknown` sends the reader to `deadline.notes` and the
    source; a wrong `open` sends them to a dead form.
    """
    dl = rec.get("deadline") or {}
    today = date.today().isoformat()
    opens, closes = dl.get("opens"), dl.get("date")
    if dl.get("type") == "rolling":
        return "rolling"
    if opens and opens > today:
        return "upcoming"
    if closes and closes < today:
        return "closed"
    if closes and closes >= today:
        # A future close date is the only positive evidence that it is open now.
        return "open"
    return "unknown"
