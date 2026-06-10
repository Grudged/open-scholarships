# Contributing

Open Scholarships is a public-good data commons. The most valuable contribution is **accurate,
well-sourced records** — and keeping existing ones fresh.

## Adding or fixing a scholarship

1. Add or edit a JSON file under `data/<STATE>/` (e.g. `data/NV/`). One scholarship per file.
2. It must validate against [`schema/scholarship.schema.json`](schema/scholarship.schema.json).
3. **Gather facts only from the primary/official source** — the sponsor's own page, a `.gov`,
   `.edu`, or the foundation's site. Never copy from a proprietary aggregator (Scholarships.com,
   Fastweb, CareerOneStop/Gale, Bold.org, etc.); their compiled lists are not ours to republish.
4. Fill in `provenance` honestly: `source_url`, `source_name`, today's date in `last_verified`,
   and the `verification_method`. Leave any fact you can't confirm as `null` — **never guess**
   amounts, GPAs, or deadlines.
5. New records start at `"status": "needs-review"`. A maintainer flips them to `"active"` after
   verifying against the source. Only `active` records are served by default.

## Helper tools

- `python tools/extract.py <url> --source-name "..."` — drafts a record from a page using the
  local Gemma model (writes to `drafts/` for review; never auto-publishes).
- `python tools/check_freshness.py [--write]` — flags dead links and passed deadlines.

**Always `git pull` before local discovery testing** — approvals land on `main` from Mission
Control, so a stale clone makes the dedupe check lie (the Mac clone was once 40 commits behind).

**Run the first post-Haiku-swap discover manually and supervised** (not via the timer): UNR +
Washoe hold 30+ scholarships Gemma never surfaced, so the first run dumps a backlog into the
review queue — `--max-records 40` bounds it to one reviewable batch.

## Validate before you PR

```bash
api/venv/bin/python -m pytest tests -q
```
