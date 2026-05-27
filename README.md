# Open Scholarships

A free, open directory of scholarships and student aid — **Nevada first** — published as
machine-readable data anyone can use, with a thin public read API on top.

Most scholarship listings are locked inside proprietary aggregators (Scholarships.com, Fastweb,
Bold.org) or licensed datasets (CareerOneStop's listings are licensed from Gale/Cengage). There
is no free, open, machine-readable source. This is that source. The **data is the product**; the
API is just a tap. The goal isn't reach — it's to be the *authoritative, current, openly-licensed*
record of the aid a Nevada student can actually get, and to grow outward from there.

**License:** data is [CC BY 4.0](LICENSE) (use it freely, just attribute). Code is [MIT](LICENSE-CODE).

**Live:** [scholarships.grudged.io](https://scholarships.grudged.io) — docs + full dataset at `/scholarships.json`, query API at `/api/scholarships?state=NV&level=high-school-senior`. CDN-served, CORS-open, no key. Auto-published from this repo on every push (Netlify static + edge function); the FastAPI service in `api/` is the internal curation/dev engine.

## Why it's built this way

- **Curation is the value.** A stale scholarship DB is worse than none — a kid who chases a dead
  link or a passed deadline is harmed. Every record carries `provenance` (`source_url`,
  `last_verified`, method) so trust is verifiable, and a freshness checker flags rot.
- **Facts from primary sources only.** Records are gathered from sponsors' own pages, `.gov`, and
  `.edu` — never copied from a proprietary aggregator's compiled list.
- **Local-authoritative beats national-shallow.** Own Nevada completely first (state programs +
  every NSHE institution + Nevada/Vegas foundations + civic awards), then expand by state.

## Data model

One JSON file per scholarship under `data/<STATE>/`, validated against
[`schema/scholarship.schema.json`](schema/scholarship.schema.json). Key fields: `name`, `sponsor`,
`type`, `award` (amount/basis/renewable), `deadline`, `eligibility` (residency, level, fields,
GPA, tags), `geo`, `links`, `provenance`, and `status`. New records start `needs-review`; only
`active` records are served by default.

## API

```
GET /healthz                      liveness + record count
GET /meta                         license, attribution, counts, states covered
GET /scholarships                 filters: state, level, field, basis, type, sponsor_type,
                                   deadline_after, amount_min, q, status, limit, offset
GET /scholarships/{id}            one record
```

Every response carries `X-License` / `X-Attribution` headers; CORS is open (any site may read it).

### Run locally

```bash
cd api
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/uvicorn app.main:app --reload --port 8930
# http://localhost:8930/meta  •  /docs for the OpenAPI explorer
```

### Maintain the data

```bash
python tools/extract.py <official-url> --source-name "..."   # Gemma-assisted draft -> drafts/
python tools/check_freshness.py [--write]                    # flag dead links / passed deadlines
api/venv/bin/python -m pytest tests -q                       # validate every record vs schema
```

## Status

v0.1 — Nevada seed (Governor Guinn Millennium, Silver State Opportunity Grant, Nevada Promise),
all `needs-review` pending source verification. First consumer: [FirstEmbark](https://firstembark.com).

*Open Scholarships by [Grudged LLC](https://github.com/Grudged/open-scholarships).*
