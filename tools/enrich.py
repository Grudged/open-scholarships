#!/usr/bin/env python3
"""Auto-fill apply link / open + close dates / amount on stub records, and FLAG where the live page
contradicts the stub, so a human's review is VERIFY-what-we-found, not type-it-in-from-scratch.

Extraction uses **Claude Haiku** (structured outputs) — the local Gemma model proved unreliable for
this (it looped, mangled JSON keys, hallucinated URLs, and missed plain-text facts). Structured
outputs guarantee schema-valid JSON, so the looping/key-mangling failure mode is gone. Fetch +
render plumbing is still reused from discover.py.

For each record it fetches the source page (rendering JS/Wix pages when a static fetch is a thin
shell) and extracts CANDIDATES:
  - apply_url:  a clearly-labeled on-page "Apply" link. We trust the page HTML's own <a> tags first;
                a model-supplied apply_url is accepted ONLY if it literally appears in the fetched
                page (kills hallucinated URLs). If there's no distinct application link, apply_url
                stays null and we flag it — we never pass off info_url as an apply link.
  - opens:      the date applications OPEN (a real window has two ends), set if a date is stated.
  - deadline:   an exact CLOSE date ONLY if a clearly-stated FUTURE date is found; a stale past date
                becomes a review flag, never a live deadline (keeps the 100%-accurate bar honest).
  - amount_max: the award amount if stated and plausible.
  - eligibility signals (gender / GPA / geography / basis): GPA fills a null; the rest are FLAGGED
    for the human, never silently rewritten — fabricated eligibility is exactly what burned us
    (a women-only, Clark-County-EXCLUDED award was stubbed as "statewide, all students").

Only fills NULLs (never overwrites a human-set value), keeps status `needs-review`, and does NOT
touch provenance.last_verified (that's the human's confirmation, set via the MC review control).

Requires ANTHROPIC_API_KEY in the environment (fails hard if missing).

    api/venv/bin/python tools/enrich.py [--dry-run] [--limit N] [--id SUBSTR] [--status needs-review|active|all]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import date
from typing import Optional
from urllib.parse import urljoin

import anthropic
from pydantic import BaseModel

import discover as d  # fetch, render_html, page_text, _num, _coerce, DATA, VALIDATOR, BASIS_ENUM

MODEL = "claude-haiku-4-5"
APPLY_RE = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
THIN_PAGE = 800  # chars of visible text below which a static fetch is probably a JS shell

# Instructions are stable across all 40 records → belongs in the cached system prefix. NOTE: Haiku's
# minimum cacheable prefix is 4096 tokens and this block is ~300, so it won't actually cache at this
# size — the cache_control is harmless and future-proof, not a live optimization. The page text (the
# big, per-record part) goes in the user turn, where it should.
SYSTEM = """You extract scholarship facts from one official web page. Use ONLY facts visibly stated
on the page. Never guess or infer; if a fact is not clearly stated, return null for it.
- opens_date / deadline_date: ISO YYYY-MM-DD. opens_date = when applications OPEN; deadline_date =
  when they CLOSE. If only a vague window is given (no exact dates), leave both null and put a short
  phrase in deadline_note instead.
- amount_max: the maximum award as a number, no $ or commas.
- basis: one of merit, need, merit-need, identity, field, service, other.
- gender_restriction: the word if the award is limited to a gender (e.g. "women"); else null.
- gpa_min: minimum GPA as a number (e.g. 3.0).
- geo_restriction: a short phrase if eligibility is limited to / EXCLUDES a specific geography
  (e.g. "northern and rural Nevada, excludes Clark County"); else null.
- apply_url: the application link ONLY if a clearly-labeled Apply/Application link is on the page."""


class PageFacts(BaseModel):
    apply_url: Optional[str] = None
    opens_date: Optional[str] = None
    deadline_date: Optional[str] = None
    deadline_note: Optional[str] = None
    amount_max: Optional[float] = None
    basis: Optional[str] = None
    gender_restriction: Optional[str] = None
    gpa_min: Optional[float] = None
    geo_restriction: Optional[str] = None


_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("ANTHROPIC_API_KEY not set — enrich needs it for Claude extraction.")
        _client = anthropic.Anthropic()
    return _client


def _plausible_amount(n: float) -> bool:
    """A believable single-award max: $250–$350k, and not a bare year (1900–2100)."""
    return 250 <= n <= 350_000 and not (1900 <= n <= 2100)


def best_page(url: str):
    """Static fetch, but fall back to a real browser render when the page is a thin JS shell
    (Wix/SPA scholarship pages return almost no text to httpx). Returns (html, resolved_url)."""
    html, resolved = d.fetch(url)
    text = d.page_text(html) if html else ""
    if len(text) < THIN_PAGE:
        rhtml, rresolved = d.render_html(url)
        if rhtml and len(d.page_text(rhtml)) > len(text):
            return rhtml, (rresolved if isinstance(rresolved, str) and rresolved.startswith("http") else (resolved or url))
    return html, (resolved or url)


def find_apply_link(html: str, base: str) -> str | None:
    for m in APPLY_RE.finditer(html):
        href, text = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
        if re.search(r"\bapply\b|application", text, re.I) or re.search(r"/apply", href, re.I):
            u = urljoin(base, href).split("#")[0]
            if u.lower().startswith("http"):
                return u
    return None


def claude_extract(name: str, text: str) -> dict:
    """Structured extraction via Claude Haiku. Returns a dict of PageFacts fields, or {} on failure."""
    try:
        resp = client().messages.parse(
            model=MODEL,
            max_tokens=1024,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f'Scholarship: "{name}"\n\nPage text:\n{text[:12000]}'}],
            output_format=PageFacts,
        )
    except anthropic.APIError as e:
        print(f"        (Claude error: {type(e).__name__})")
        return {}
    return resp.parsed_output.model_dump() if resp.parsed_output else {}


def enrich(rec: dict) -> tuple[list[str], list[str]]:
    """Fill missing fields in-place; return (changed_fields, review_flags)."""
    links, award, dl = rec.setdefault("links", {}), rec.setdefault("award", {}), rec.setdefault("deadline", {})
    elig = rec.setdefault("eligibility", {})
    info_url = links.get("info_url")
    if not info_url:
        return [], []
    html, resolved = best_page(info_url)
    changed: list[str] = []
    flags: list[str] = []

    # apply_url: a real on-page Apply link ONLY — never fabricate it from info_url.
    if not links.get("apply_url"):
        apply = find_apply_link(html, resolved or info_url) if html else None
        if apply and apply != (resolved or info_url):
            links["apply_url"] = apply
            changed.append("apply_url=on-page")
        else:
            flags.append("No distinct application link on the source page — applicants apply via the info page.")

    if not html:
        flags.append("Source page could not be fetched/rendered — verify everything by hand.")
        return changed, flags

    g = claude_extract(rec.get("name", ""), d.page_text(html))
    if not g:
        flags.append("Auto-extraction returned nothing — verify by hand.")
        return changed, flags

    # A model-supplied apply_url is accepted ONLY if it literally appears in the fetched page
    # (deterministic anti-hallucination guard — Gemma invented a misspelled domain here).
    gu = g.get("apply_url")
    if not links.get("apply_url") and isinstance(gu, str) and gu.startswith("http"):
        gu = gu.split("#")[0]
        if gu != (resolved or info_url) and gu in html:
            links["apply_url"] = gu
            changed.append("apply_url=claude")
            flags = [f for f in flags if not f.startswith("No distinct application link")]

    # amount
    if award.get("amount_max") is None and g.get("amount_max") is not None:
        n = d._num(g.get("amount_max"))
        if n and _plausible_amount(n):
            award["amount_max"] = int(n)
            changed.append(f"amount_max={int(n)}")

    # opens — a stated open date is informative whether past or future
    if not dl.get("opens"):
        od = g.get("opens_date")
        if isinstance(od, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", od):
            dl["opens"] = od
            dl.setdefault("type", "annual")
            changed.append(f"opens={od}")

    # deadline (close) — only set a clearly-stated FUTURE date; past → flag, never a live deadline
    if not dl.get("date"):
        gd = g.get("deadline_date")
        if isinstance(gd, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", gd):
            if gd >= date.today().isoformat():
                dl["date"] = gd
                dl.setdefault("type", "annual")
                changed.append(f"deadline={gd}")
            else:
                flags.append(f"Source shows a past deadline {gd} — confirm the current cycle.")
        elif isinstance(g.get("deadline_note"), str) and g["deadline_note"].strip():
            note = g["deadline_note"].strip()[:80]
            if not dl.get("notes") or "verify" in (dl.get("notes") or "").lower():
                dl["notes"] = note + " (auto-found — verify)"
                changed.append("deadline_note")

    if dl.get("opens") and dl.get("date") and dl["date"] < date.today().isoformat():
        flags.append("Both open and close dates are in the past — looks like last cycle; verify next cycle.")

    # ---- eligibility: GPA fills a null; gender/geo/basis are FLAGGED, never silently rewritten ----
    blob = " ".join(str(x) for x in (elig.get("other") or []) + (elig.get("tags") or [])
                     + [rec.get("name", ""), rec.get("sponsor", "")]).lower()

    gx = g.get("gender_restriction")
    if isinstance(gx, str) and gx.strip() and gx.strip().lower() not in blob:
        flags.append(f"Source restricts to {gx.strip().lower()} — not reflected in eligibility; confirm and restrict.")

    gp = d._num(g.get("gpa_min"))
    if gp and 0 < gp <= 4.5:
        cur = elig.get("gpa_min")
        if cur is None:
            elig["gpa_min"] = gp
            changed.append(f"gpa_min={gp}")
        elif abs(cur - gp) > 0.01:
            flags.append(f"Source GPA min {gp} differs from stub {cur} — verify.")

    geo = g.get("geo_restriction")
    if isinstance(geo, str) and geo.strip():
        flags.append(f"Geography: \"{geo.strip()[:100]}\" — verify scope/counties (stub scope={rec.get('geo', {}).get('scope')}).")

    bx = d._coerce(g.get("basis"), set(d.BASIS_ENUM), None)
    if bx and award.get("basis") and bx != award.get("basis"):
        flags.append(f"Source basis looks like '{bx}'; stub says '{award.get('basis')}' — verify.")

    return changed, flags


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--id", help="only records whose id contains this substring")
    ap.add_argument("--status", default="needs-review", choices=["needs-review", "active", "all"])
    args = ap.parse_args()

    paths = sorted(d.DATA.rglob("*.json"))
    done = touched = 0
    for p in paths:
        rec = json.loads(p.read_text())
        if args.status != "all" and rec.get("status") != args.status:
            continue
        if args.id and args.id not in rec.get("id", ""):
            continue
        if args.limit and done >= args.limit:
            break
        done += 1
        time.sleep(d.FETCH_DELAY)
        changed, flags = enrich(rec)
        if flags:
            rec["review_flags"] = flags
        else:
            rec.pop("review_flags", None)
        errs = list(d.VALIDATOR.iter_errors(rec))
        if errs:
            print(f"  [{rec['id']}] SCHEMA ERROR, not written: {errs[0].message}")
            continue
        print(f"  [{rec['id']}] {', '.join(changed) if changed else 'no auto-fills'}{('  ⚑' + str(len(flags))) if flags else ''}")
        for fl in flags:
            print(f"        ⚑ {fl}")
        if (changed or flags) and not args.dry_run:
            p.write_text(json.dumps(rec, indent=2) + "\n")
            touched += 1
    print(f"\n{'(dry run) ' if args.dry_run else ''}updated {touched}/{done} record(s).")


if __name__ == "__main__":
    main()
