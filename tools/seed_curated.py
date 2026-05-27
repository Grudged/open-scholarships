#!/usr/bin/env python3
"""Curate a vetted batch of well-known scholarships into the review queue.

Same honest pattern as the original Nevada seeds: real programs + their OFFICIAL source URLs,
conservative data (amounts/deadlines left null rather than guessed), every record `needs-review`
so a human verifies the specifics against the source before it goes live. This is deliberate
curation, not a flaky crawl — reliable volume that a person then approves.

Each entry's info_url is fetched first; dead links are skipped so we never queue a broken record.

    api/venv/bin/python tools/seed_curated.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import discover as d  # reuse fetch + schema validator + slug  # noqa: E402

TODAY = date.today().isoformat()

# (name, sponsor, sponsor_type, type, basis, [levels], info_url, [eligibility notes])
CURATED = [
    ("The Gates Scholarship", "The Gates Scholarship", "foundation", "scholarship", "need",
     ["high-school-senior"], "https://www.thegatesscholarship.org/scholarship",
     ["Pell-eligible high school seniors from minority backgrounds", "Last-dollar; full cost of attendance"]),
    ("QuestBridge National College Match", "QuestBridge", "foundation", "scholarship", "need",
     ["high-school-senior"], "https://www.questbridge.org/high-school-students/national-college-match",
     ["High-achieving high school seniors from low-income households", "Full four-year scholarships to partner colleges"]),
    ("Coca-Cola Scholars Program", "Coca-Cola Scholars Foundation", "foundation", "scholarship", "merit",
     ["high-school-senior"], "https://www.coca-colascholarsfoundation.org/programs/coca-cola-scholars-program/",
     ["Achievement-based award for graduating high school seniors"]),
    ("Hispanic Scholarship Fund", "Hispanic Scholarship Fund", "foundation", "scholarship", "merit-need",
     ["high-school-senior", "undergraduate", "graduate"], "https://www.hsf.net/scholarship",
     ["Students of Hispanic heritage", "Minimum GPA and FAFSA/Dream Act application required (verify)"]),
    ("Jack Kent Cooke Foundation College Scholarship", "Jack Kent Cooke Foundation", "foundation", "scholarship", "merit-need",
     ["high-school-senior"], "https://www.jkcf.org/our-scholarships/college-scholarship-program/",
     ["High-achieving high school seniors with financial need"]),
    ("Dell Scholars Program", "Michael & Susan Dell Foundation", "foundation", "scholarship", "need",
     ["high-school-senior"], "https://www.dellscholars.org/scholarship/",
     ["Students in approved college-readiness programs; grit-focused", "Verify program participation requirement"]),
    ("Horatio Alger National Scholarship", "Horatio Alger Association", "foundation", "scholarship", "need",
     ["high-school-senior"], "https://scholars.horatioalger.org/",
     ["Students who have faced and overcome adversity, with financial need"]),
    ("Ron Brown Scholar Program", "Ron Brown Scholar Fund", "foundation", "scholarship", "identity",
     ["high-school-senior"], "https://www.ronbrown.org/",
     ["Black/African American high school seniors"]),
    ("Jackie Robinson Foundation Scholarship", "Jackie Robinson Foundation", "foundation", "scholarship", "identity",
     ["high-school-senior"], "https://www.jackierobinson.org/apply/",
     ["Minority high school seniors with financial need and leadership"]),
    ("Elks National Foundation Most Valuable Student", "Elks National Foundation", "civic", "scholarship", "merit-need",
     ["high-school-senior"], "https://www.elks.org/scholars/scholarships/mvs.cfm",
     ["U.S. high school seniors; leadership + financial need"]),
    ("Coolidge Scholarship", "Calvin Coolidge Presidential Foundation", "foundation", "scholarship", "merit",
     ["high-school-senior"], "https://www.coolidgescholars.org/",
     ["Full-ride merit scholarship; apply as a high school junior (verify timing)"]),
    ("Davidson Fellows Scholarship", "Davidson Institute", "foundation", "scholarship", "merit",
     ["high-school", "undergraduate"], "https://www.davidsongifted.org/gifted-programs/fellows-scholarship/",
     ["Students 18 or younger with a significant project in STEM, literature, music, or philosophy"]),
    ("GE-Reagan Foundation Scholarship", "Ronald Reagan Presidential Foundation", "foundation", "scholarship", "merit",
     ["high-school-senior"], "https://www.reaganfoundation.org/education/scholarship-programs/ge-reagan-foundation-scholarship-program/",
     ["Leadership, drive, integrity, citizenship; financial need considered"]),
    ("National Merit Scholarship Program", "National Merit Scholarship Corporation", "foundation", "scholarship", "merit",
     ["high-school-senior"], "https://www.nationalmerit.org/",
     ["Qualify via the PSAT/NMSQT in the junior year"]),
]


def build(name, sponsor, sponsor_type, typ, basis, levels, info_url, notes) -> dict:
    return {
        "id": f"us-{d._slug(name)}",
        "name": name,
        "sponsor": sponsor,
        "sponsor_type": sponsor_type,
        "type": typ,
        "award": {"amount_min": None, "amount_max": None, "currency": "USD", "basis": basis,
                  "renewable": None, "notes": None},
        "deadline": {"type": "annual", "date": None, "notes": "Verify the current cycle's deadline against the source."},
        "eligibility": {"residency": ["US"], "education_level": levels, "fields_of_study": [],
                        "gpa_min": None, "citizenship": [], "other": notes, "tags": ["national"]},
        "geo": {"state": None, "scope": "national", "counties": []},
        "links": {"info_url": info_url, "apply_url": None},
        "provenance": {"source_url": info_url, "source_name": sponsor, "last_verified": TODAY,
                       "verification_method": "research-summary", "added": TODAY},
        "status": "needs-review",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    existing = d.existing_keys()
    out_dir = d.DATA / "US"
    written = 0
    for entry in CURATED:
        rec = build(*entry)
        name_key = d._slug(rec["name"])
        if name_key in existing:
            print(f"  skip (already have): {rec['name']}")
            continue
        # confirm the official link is live before queueing — never queue a dead source
        html, info = d.fetch(rec["links"]["info_url"])
        if not html:
            print(f"  SKIP (dead link {info}): {rec['name']}  {rec['links']['info_url']}")
            continue
        errors = list(d.VALIDATOR.iter_errors(rec))
        if errors:
            print(f"  SKIP (invalid): {rec['name']} — {errors[0].message}")
            continue
        print(f"  + {rec['id']}")
        if not args.dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{rec['id']}.json").write_text(json.dumps(rec, indent=2) + "\n")
            written += 1

    print(f"\n{'(dry run) ' if args.dry_run else ''}{written} record(s) written to data/US/ as needs-review.")


if __name__ == "__main__":
    main()
