"""Freshness / link-rot checker. Reports dead source/apply links and past-deadline records so a
human can re-verify. Read-only by default; --write flips obviously-stale 'active' records to
'needs-review' so a kid never chases a dead link or a passed deadline.

    cd ~/repos/open-scholarships && python tools/check_freshness.py [--write]
"""
from __future__ import annotations

import argparse
import json
from datetime import date
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
        problems = []
        if alive(prov.get("source_url")) is False:
            problems.append("dead source_url")
        if links.get("apply_url") and alive(links["apply_url"]) is False:
            problems.append("dead apply_url")
        if dl.get("date") and dl["date"] < today:
            problems.append(f"deadline passed ({dl['date']})")
        if problems:
            issues += 1
            print(f"[{rec['id']}] {', '.join(problems)}")
            if args.write and rec.get("status") == "active":
                rec["status"] = "needs-review"
                path.write_text(json.dumps(rec, indent=2) + "\n")
                print(f"    -> set {rec['id']} to needs-review")

    print(f"\n{issues} record(s) need attention." if issues else "\nAll records fresh.")


if __name__ == "__main__":
    main()
