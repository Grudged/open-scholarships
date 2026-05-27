#!/usr/bin/env python3
"""Automated scholarship discovery.

Crawls a curated set of TRUSTED official sources (tools/sources.json — .gov/.edu/foundation
domains only, never the open web), extracts candidate scholarships with the local Gemma model,
normalizes them to the schema, validates, dedupes against what we already have, and writes the
new ones into the dataset as `status: needs-review` — i.e. straight into the Mission Control
review queue. Nothing here publishes; only a human approve in MC flips a record to `active`.

Scope is the trust boundary: crawling only within configured official domains is what keeps
quality high and keeps scam/aggregator data out. The human approve step is the final gate.

    api/venv/bin/python tools/discover.py [--dry-run] [--source NAME] [--max-records N]

Runs on Arch via a weekly timer; reaches Gemma on the Mac via MLX_URL.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from jsonschema import Draft202012Validator

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
SCHEMA = json.loads((REPO / "schema" / "scholarship.schema.json").read_text())
SOURCES = json.loads((REPO / "tools" / "sources.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA)

MLX_URL = os.getenv("MLX_URL", "http://192.168.0.79:8321").rstrip("/")  # Mac MLX, reachable from Arch
UA = "Mozilla/5.0 (compatible; OpenScholarshipsBot/0.1; +https://github.com/Grudged/open-scholarships)"
LINK_RE = re.compile(r"scholarship|grant|aid|award|fellowship|financial", re.I)
FETCH_DELAY = 1.0  # be polite

LEVEL_ENUM = SCHEMA["properties"]["eligibility"]["properties"]["education_level"]["items"]["enum"]
SPONSOR_ENUM = SCHEMA["properties"]["sponsor_type"]["enum"]
TYPE_ENUM = SCHEMA["properties"]["type"]["enum"]
BASIS_ENUM = SCHEMA["properties"]["award"]["properties"]["basis"]["enum"]

EXTRACT_PROMPT = """From the official source page below, extract EVERY scholarship / grant / aid
program it describes, as a JSON array. Use ONLY facts present on the page — NEVER guess amounts,
GPAs, or deadlines (use null). Each array item has these keys:
  name, sponsor, sponsor_type (state|federal|institution|foundation|civic|employer|private|other),
  type (scholarship|grant|waiver|fellowship|loan-forgiveness|prize),
  summary (one sentence), amount_max (number or null),
  basis (merit|need|merit-need|identity|field|service|other|null),
  deadline (text or null),
  education_level (array from: high-school-senior|undergraduate|community-college|graduate|vocational|any),
  eligibility_notes (array of short strings).
If the page describes no specific named scholarship, return []. Output ONLY the JSON array.

Source: {name}
URL: {url}

Page text:
{text}
"""


def fetch(url: str):
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=25)
        r.raise_for_status()
    except httpx.HTTPError as e:
        return None, f"fetch failed: {type(e).__name__}"
    if "html" not in r.headers.get("content-type", "").lower():
        return None, "not html"
    return r.text, str(r.url)


def render_html(url: str):
    """Render a JS page with Playwright (lazily imported so the script still loads where Playwright
    isn't installed, e.g. Arch). Official scholarship databases are JS apps that a static fetch
    sees as ~empty, so this is how we reach the real volume. Mac-side only."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "playwright not installed"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=UA)
            page.goto(url, wait_until="networkidle", timeout=35000)
            page.wait_for_timeout(1500)  # let late XHR-driven lists settle
            html = page.content()
            browser.close()
            return html, url
    except Exception as e:  # noqa: BLE001
        return None, f"render failed: {type(e).__name__}"


def get_page(url: str, render: bool):
    return render_html(url) if render else fetch(url)


def page_text(html: str) -> str:
    h = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    t = re.sub(r"(?s)<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", t).strip()


def candidate_links(html: str, base: str, domain: str) -> list[str]:
    out: list[str] = []
    for m in re.finditer(r'href=["\']([^"\']+)', html):
        u = urljoin(base, m.group(1)).split("#")[0]
        p = urlparse(u)
        if p.scheme in ("http", "https") and p.netloc.endswith(domain) and LINK_RE.search(p.path):
            out.append(u)
    return list(dict.fromkeys(out))


def gemma_extract(name: str, url: str, text: str) -> list[dict]:
    body = {
        "messages": [
            {"role": "system", "content": "You extract structured data. Output only valid JSON."},
            {"role": "user", "content": EXTRACT_PROMPT.format(name=name, url=url, text=text[:14000])},
        ],
        "temperature": 0.1,
        "max_tokens": 2200,
    }
    try:
        r = httpx.post(f"{MLX_URL}/v1/chat/completions", json=body, timeout=180)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, ValueError):
        return []
    m = re.search(r"\[.*\]", content, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def _num(v):
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(re.sub(r"[^0-9.]", "", str(v))) if v and re.search(r"\d", str(v)) else None
    except (TypeError, ValueError):
        return None


def _coerce(v, allowed, default):
    s = v.strip().lower() if isinstance(v, str) else v
    return s if s in allowed else default


def to_record(item: dict, source: dict, page_url: str) -> dict | None:
    name = (item.get("name") or "").strip()
    if len(name) < 4:
        return None
    state = source["state"]
    national = state.upper() == "US"
    prefix = "us" if national else state.lower()
    levels = [x for x in (item.get("education_level") or []) if x in LEVEL_ENUM] or ["any"]
    rec = {
        "id": f"{prefix}-{_slug(name)}",
        "name": name,
        "sponsor": (item.get("sponsor") or source["name"]).strip(),
        "sponsor_type": _coerce(item.get("sponsor_type"), SPONSOR_ENUM, source.get("default_sponsor_type", "other")),
        "type": _coerce(item.get("type"), TYPE_ENUM, "scholarship"),
        "award": {
            "amount_min": None, "amount_max": _num(item.get("amount_max")), "currency": "USD",
            "basis": _coerce(item.get("basis"), BASIS_ENUM, None), "renewable": None, "notes": None,
        },
        "deadline": {"type": "unknown", "date": None, "notes": (item.get("deadline") or None)},
        "eligibility": {
            "residency": ["US"] if national else [state], "education_level": levels, "fields_of_study": [], "gpa_min": None,
            "citizenship": [], "tags": ["national"] if national else [state.lower()],
            "other": [s for s in (item.get("eligibility_notes") or []) if isinstance(s, str)][:6],
        },
        "geo": {"state": None if national else state, "scope": "national" if national else "statewide", "counties": []},
        "links": {"info_url": page_url, "apply_url": None},
        "provenance": {
            "source_url": page_url, "source_name": source["name"],
            "last_verified": date.today().isoformat(), "verification_method": "gemma-extract",
            "added": date.today().isoformat(),
        },
        "status": "needs-review",
    }
    summary = (item.get("summary") or "").strip()
    if summary:
        rec["summary"] = summary[:300]
    errors = list(VALIDATOR.iter_errors(rec))
    return rec if not errors else None


def existing_keys() -> set[str]:
    """Dedupe identity = slug of the scholarship NAME (independent of how its id was assigned), so
    a discovered record matching a hand-seeded one (e.g. 'Governor Guinn Millennium', seeded as
    nv-ggms-millennium) is still caught. Covers active + needs-review + archived — so a rejected
    record acts as a tombstone and won't be re-queued on the next run."""
    keys = set()
    for p in DATA.rglob("*.json"):
        try:
            keys.add(_slug(json.loads(p.read_text())["name"]))
        except (json.JSONDecodeError, KeyError):
            pass
    return keys


def crawl_source(source: dict, seen_ids: set[str], dry: bool, budget: int) -> list[dict]:
    seed = source["url"]
    domain = urlparse(seed).netloc
    render = bool(source.get("render"))
    html, info = get_page(seed, render)
    pages = [seed]
    if html:
        pages += candidate_links(html, seed, domain)
    pages = list(dict.fromkeys(pages))[: source.get("max_pages", 12)]
    print(f"\n[{source['name']}] {seed}\n  {'(seed unreachable: ' + str(info) + ') ' if not html else ''}{len(pages)} page(s) to scan")

    found: list[dict] = []
    for url in pages:
        if len(found) >= budget:
            break
        time.sleep(FETCH_DELAY)
        h, resolved = (html, info) if url == seed and html else get_page(url, render)
        if not h:
            continue
        items = gemma_extract(source["name"], resolved or url, page_text(h))
        for item in items:
            rec = to_record(item, source, resolved or url)
            if not rec:
                continue
            key = _slug(rec["name"])
            if key in seen_ids:
                continue  # already have this scholarship (any status) — dedupe / tombstone
            seen_ids.add(key)
            found.append(rec)
            print(f"    + {rec['id']}  ({rec['name'][:55]})")
            if len(found) >= budget:
                break
    return found


def git_publish(n: int):
    d = str(REPO)
    try:
        subprocess.run(["git", "-C", d, "add", "data"], check=True, capture_output=True, timeout=15)
        subprocess.run(["git", "-C", d, "commit", "-m", f"discover: queue {n} new scholarship(s) for review"],
                       check=True, capture_output=True, timeout=15)
        subprocess.run(["git", "-C", d, "pull", "--rebase", "origin", "main"], check=True, capture_output=True, timeout=30)
        subprocess.run(["git", "-C", d, "push", "origin", "main"], check=True, capture_output=True, timeout=30)
        print("  committed + pushed.")
    except Exception as e:  # noqa: BLE001
        print(f"  git publish skipped/failed (records still written): {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="extract + print, but don't write or commit")
    ap.add_argument("--source", help="only run the source whose name contains this (case-insensitive)")
    ap.add_argument("--max-records", type=int, default=40, help="cap new records this run")
    args = ap.parse_args()

    sources = SOURCES
    if args.source:
        sources = [s for s in SOURCES if args.source.lower() in s["name"].lower()]
    seen = existing_keys()
    print(f"Discovery run — {len(seen)} existing records, {len(sources)} source(s), MLX={MLX_URL}"
          + (" [DRY RUN]" if args.dry_run else ""))

    new: list[dict] = []
    for s in sources:
        if len(new) >= args.max_records:
            break
        new += crawl_source(s, seen, args.dry_run, args.max_records - len(new))

    print(f"\n=== {len(new)} new candidate(s) for review ===")
    if args.dry_run:
        print("(dry run — nothing written)")
        return
    for rec in new:
        out = DATA / rec["geo"]["state"] / f"{rec['id']}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rec, indent=2) + "\n")
    if new:
        git_publish(len(new))
        print(f"Queued {len(new)} record(s) → review them in Mission Control → Scholarships.")


if __name__ == "__main__":
    main()
