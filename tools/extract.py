"""Gemma-assisted draft extractor: turn an official scholarship page into a DRAFT record for
human review. It never writes to data/ — drafts land in drafts/ and a person verifies the facts
against the source before promoting them. Uses the local MLX server (Gemma), OpenAI-compatible.

    cd ~/repos/open-scholarships && python tools/extract.py <source_url> --source-name "Nevada State Treasurer"

MLX_URL defaults to http://localhost:8321 (the always-on Gemma server on the Mac).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

import httpx

MLX_URL = os.getenv("MLX_URL", "http://localhost:8321").rstrip("/")
REPO = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((REPO / "schema" / "scholarship.schema.json").read_text())
UA = "Mozilla/5.0 (compatible; OpenScholarshipsBot/0.1; +https://github.com/Grudged/open-scholarships)"

PROMPT = """You extract ONE scholarship/aid record from an official source page as STRICT JSON
matching the JSON Schema below. Use ONLY facts that appear on the page. If a fact is not present,
use null (or an empty array) — NEVER guess amounts, GPAs, deadlines, or eligibility. Output ONLY
the JSON object, no prose.

JSON Schema:
{schema}

Source name: {source_name}
Source URL: {url}

Page text:
{text}
"""


def fetch_text(url: str) -> str:
    r = httpx.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=30)
    r.raise_for_status()
    html = r.text
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:12000]


def gemma(messages: list[dict]) -> str:
    r = httpx.post(
        f"{MLX_URL}/v1/chat/completions",
        json={"messages": messages, "temperature": 0.1, "max_tokens": 1400},
        timeout=180,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Draft a scholarship record from an official page (human-reviewed).")
    ap.add_argument("url")
    ap.add_argument("--source-name", required=True, help="Human name of the source, e.g. 'Nevada State Treasurer'")
    args = ap.parse_args()

    text = fetch_text(args.url)
    content = gemma([
        {"role": "system", "content": "You are a careful data-extraction assistant. Output only valid JSON."},
        {"role": "user", "content": PROMPT.format(
            schema=json.dumps(SCHEMA), source_name=args.source_name, url=args.url, text=text)},
    ])

    match = re.search(r"\{.*\}", content, re.S)
    if not match:
        print("Model did not return JSON:\n" + content, file=sys.stderr)
        sys.exit(1)
    rec = json.loads(match.group(0))

    # Force honest provenance + human gate regardless of what the model produced.
    rec.setdefault("provenance", {})
    rec["provenance"].update({
        "source_url": args.url,
        "source_name": args.source_name,
        "last_verified": date.today().isoformat(),
        "verification_method": "gemma-extract",
        "added": date.today().isoformat(),
    })
    rec["status"] = "needs-review"

    drafts = REPO / "drafts"
    drafts.mkdir(exist_ok=True)
    out = drafts / f"{rec.get('id', 'draft')}.json"
    out.write_text(json.dumps(rec, indent=2) + "\n")
    print(f"Draft written to {out}")
    print("REVIEW IT: verify every fact against the source, then move it into data/<STATE>/ and flip status to 'active'.")


if __name__ == "__main__":
    main()
