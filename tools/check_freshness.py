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


def link_state(url: str | None) -> str:
    """'ok' | 'gone' | 'blocked' | 'unreachable' | 'none'.

    **Blocked is not gone.** A 403/429 means a WAF refused to let us look, not that the page
    stopped existing — LULAC and Tylenol both 403 this checker while serving fine in a browser.
    Treating those as dead links is what made the tool cry wolf, and acting on it would pull
    live scholarships out of the commons.

    We keep identifying ourselves honestly in the User-Agent and do NOT spoof a browser to get
    past a WAF. Evading the block is the adversarial line this project already decided not to
    cross when the crawler was retired; the honest answer is "we couldn't check", and a human
    can open it in a browser.
    """
    if not url:
        return "none"
    try:
        r = httpx.head(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=20)
        if r.status_code >= 400:  # some servers reject HEAD — retry with GET
            r = httpx.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=20)
        code = r.status_code
        if code < 400:
            return "ok"
        if code in (401, 403, 429):
            return "blocked"
        if code >= 500:
            return "unreachable"   # server-side trouble, often transient
        return "gone"              # 404/410 and other hard client errors
    except httpx.HTTPError:
        return "unreachable"




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
        # record out of the public dataset — so only a link that is GONE counts, never one we
        # were merely blocked from checking.
        problems = []
        notices_links = []
        for label, url in (("source_url", prov.get("source_url")),
                           ("apply_url", links.get("apply_url"))):
            state = link_state(url)
            if state == "gone":
                problems.append(f"dead {label}")
            elif state == "blocked":
                notices_links.append(f"{label} blocked to bots — open it in a browser to confirm")
            elif state == "unreachable":
                notices_links.append(f"{label} unreachable (timeout/5xx) — may be transient")
        # SOFT: needs a human to look, but the record is not known-wrong. Printed, never written.
        notices = list(notices_links)
        # A passed deadline is NOT grounds for removal. _availability() computes `closed` for
        # exactly this case and the design is explicit that annual awards recur and stay served —
        # "closed for this cycle" != "gone". This used to be a hard problem, so --write would have
        # pulled recurring awards out of the commons on the very rule that says to keep them.
        if dl.get("date") and dl["date"] < today:
            notices.append(f"deadline passed ({dl['date']}) — reads as 'closed', still served")
        # Every date check this tool had was gated on a CLOSE date — and most records have none,
        # so they were unreachable by it and aged forever without ever being flagged. The two
        # below cover that blind spot; they are the only checks that can see a dateless record.
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
