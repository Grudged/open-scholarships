"""Freshness / link-rot checker. Reports dead source/apply links and past-deadline records so a
human can re-verify. Read-only by default; --write flips obviously-stale 'active' records to
'needs-review' so a kid never chases a dead link or a passed deadline.

    cd ~/repos/open-scholarships && python tools/check_freshness.py [--write]
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
UA = "Mozilla/5.0 (compatible; OpenScholarshipsBot/0.1; +https://github.com/Grudged/open-scholarships)"


def alive(url: str | None) -> bool | None:
    """True/False if reachable, None if no URL to check."""
    if not url:
        return None
    try:
        r = httpx.head(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=20)
        if r.status_code >= 400:  # some servers reject HEAD — retry with GET
            r = httpx.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=20)
        return r.status_code < 400
    except httpx.HTTPError:
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="flip stale 'active' records to 'needs-review'")
    args = ap.parse_args()

    today = date.today().isoformat()
    issues = 0
    for path in sorted(DATA.rglob("*.json")):
        rec = json.loads(path.read_text())
        prov, links, dl = rec.get("provenance", {}), rec.get("links", {}), rec.get("deadline", {})
        stale_cutoff = (date.today() - timedelta(days=330)).isoformat()
        lv = prov.get("last_verified")

        # HARD: evidence the record is actively wrong. Only these justify --write pulling a
        # record out of the public dataset.
        problems = []
        if alive(prov.get("source_url")) is False:
            problems.append("dead source_url")
        if links.get("apply_url") and alive(links["apply_url"]) is False:
            problems.append("dead apply_url")
        if dl.get("date") and dl["date"] < today:
            problems.append(f"deadline passed ({dl['date']})")

        # SOFT: needs a human to look, but the record is not known-wrong. Printed, never written.
        notices = []
        # Every date check above is gated on a CLOSE date — and most records have none, so they
        # were unreachable by this tool and aged forever without ever being flagged. These two
        # cover that blind spot.
        if lv and lv < stale_cutoff:
            notices.append(f"not re-verified since {lv} (annual recheck)")
        opens = dl.get("opens")
        if opens and not dl.get("date") and opens < stale_cutoff:
            notices.append(f"opens date {opens} is from a prior cycle and there is no close date")

        if problems or notices:
            issues += 1
            label = ", ".join(problems + [f"(soft) {n}" for n in notices])
            print(f"[{rec['id']}] {label}")
            # Soft notices must NEVER flip status. Most of the dataset carries no close date, so
            # writing on the 330-day recheck would move ~90% of records to needs-review at once —
            # and the public API serves active-only, so that empties the commons in one command.
            if args.write and problems and rec.get("status") == "active":
                rec["status"] = "needs-review"
                path.write_text(json.dumps(rec, indent=2) + "\n")
                print(f"    -> set {rec['id']} to needs-review")

    print(f"\n{issues} record(s) need attention." if issues else "\nAll records fresh.")


if __name__ == "__main__":
    main()
