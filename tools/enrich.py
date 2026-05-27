#!/usr/bin/env python3
"""Auto-fill apply link / deadline / amount on records that are missing them, so a human's review
is VERIFY-what-we-found, not type-it-in-from-scratch. Reuses discover.py's fetch + Gemma plumbing.

For each record missing a field, it fetches the record's source page and extracts CANDIDATES:
  - apply_url:  an on-page "Apply" link, else the info_url (the official page is where you apply)
  - deadline:   amount/window text + an exact date ONLY if a clearly-stated FUTURE date is found
                (a stale past date is recorded as a "previously listed … verify" note, never set as
                the live deadline — keeps the 100%-accurate bar honest)
  - amount_max: the award amount if stated
Only fills NULLs (never overwrites a human-set value), keeps status `needs-review`, and does NOT
touch provenance.last_verified (that's the human's confirmation, set via the MC deadline control).

    api/venv/bin/python tools/enrich.py [--dry-run] [--limit N] [--status needs-review|active|all]
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import date
from urllib.parse import urljoin

import httpx

import discover as d  # fetch, page_text, MLX_URL, DATA

ENRICH_PROMPT = """For the scholarship "{name}" on this official page, extract ONLY facts visibly on the page, as a JSON object:
  apply_url: the direct application link if one is clearly on the page, else null
  deadline_date: the application deadline as YYYY-MM-DD ONLY if a specific date is clearly stated, else null
  deadline_note: a short phrase (<=80 chars) for the deadline timing if given (e.g. "closes early December", "opens in August"), else null
  amount_max: the maximum award as a plain number (no $ or commas), else null
Never guess. If something isn't clearly stated, use null. Output ONLY the JSON object.

Page text:
{text}
"""

APPLY_RE = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)


def find_apply_link(html: str, base: str) -> str | None:
    for m in APPLY_RE.finditer(html):
        href, text = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
        if re.search(r"\bapply\b|application", text, re.I) or re.search(r"/apply", href, re.I):
            u = urljoin(base, href).split("#")[0]
            if u.lower().startswith("http"):
                return u
    return None


def gemma_one(name: str, text: str) -> dict:
    body = {
        "messages": [
            {"role": "system", "content": "You extract structured data. Output only valid JSON."},
            {"role": "user", "content": ENRICH_PROMPT.format(name=name, text=text[:8000])},
        ],
        "temperature": 0.1, "max_tokens": 400,
    }
    try:
        r = httpx.post(f"{d.MLX_URL}/v1/chat/completions", json=body, timeout=180)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, ValueError):
        return {}
    start = next((i for i, c in enumerate(content) if c == "{"), None)
    if start is None:
        return {}
    try:
        val, _ = json.JSONDecoder().raw_decode(content[start:])
        return val if isinstance(val, dict) else {}
    except ValueError:
        return {}


def enrich(rec: dict) -> list[str]:
    """Fill missing fields in-place; return a list of what changed."""
    links, award, dl = rec.setdefault("links", {}), rec.setdefault("award", {}), rec.setdefault("deadline", {})
    info_url = links.get("info_url")
    if not info_url:
        return []
    html, resolved = d.get_page(info_url, render=False)
    changed: list[str] = []

    # apply_url: on-page Apply link → else the official info page itself
    if not links.get("apply_url"):
        apply = find_apply_link(html, resolved or info_url) if html else None
        links["apply_url"] = apply or info_url
        changed.append(f"apply_url={'on-page' if apply else 'info_url'}")

    if not html:
        return changed

    g = gemma_one(rec.get("name", ""), d.page_text(html))
    if not g:
        return changed

    if not links.get("apply_url") and isinstance(g.get("apply_url"), str) and g["apply_url"].startswith("http"):
        links["apply_url"] = g["apply_url"]; changed.append("apply_url=gemma")

    if award.get("amount_max") is None and g.get("amount_max") is not None:
        n = d._num(g.get("amount_max"))
        if n:
            award["amount_max"] = n; changed.append(f"amount_max={int(n)}")

    # deadline: only set an EXACT date if it's a clearly-stated FUTURE date; otherwise keep it as a
    # candidate note for the human to verify (never auto-publish a stale/past date as the deadline).
    if not dl.get("date"):
        gd = g.get("deadline_date")
        if isinstance(gd, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", gd):
            if gd >= date.today().isoformat():
                dl["date"] = gd; dl.setdefault("type", "annual"); changed.append(f"deadline_date={gd}")
            else:
                note = f"previously listed {gd} — verify current cycle"
                dl["notes"] = (dl.get("notes") + " · " if dl.get("notes") else "") + note
                changed.append("deadline=past-date→note")
        elif isinstance(g.get("deadline_note"), str) and g["deadline_note"].strip():
            n = g["deadline_note"].strip()[:80]
            if not dl.get("notes") or "verify" in (dl.get("notes") or "").lower():
                dl["notes"] = n + " (auto-found — verify)"; changed.append("deadline_note")
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--status", default="needs-review", choices=["needs-review", "active", "all"])
    args = ap.parse_args()

    paths = sorted(d.DATA.rglob("*.json"))
    done = filled = 0
    for p in paths:
        rec = json.loads(p.read_text())
        if args.status != "all" and rec.get("status") != args.status:
            continue
        links, award, dl = rec.get("links", {}), rec.get("award", {}), rec.get("deadline", {})
        if links.get("apply_url") and dl.get("date") and award.get("amount_max") is not None:
            continue  # already complete
        if args.limit and done >= args.limit:
            break
        done += 1
        time.sleep(d.FETCH_DELAY)
        changes = enrich(rec)
        if changes:
            filled += 1
            print(f"  [{rec['id']}] {', '.join(changes)}")
            if not args.dry_run:
                p.write_text(json.dumps(rec, indent=2) + "\n")
        else:
            print(f"  [{rec['id']}] (nothing extractable)")
    print(f"\n{'(dry run) ' if args.dry_run else ''}enriched {filled}/{done} record(s).")


if __name__ == "__main__":
    main()
