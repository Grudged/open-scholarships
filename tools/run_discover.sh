#!/usr/bin/env bash
# Weekly discovery wrapper (Arch timer). discover.py commits+pushes its own records but
# swallows git errors — this wrapper surfaces them, writes the warehouse heartbeat, and
# alerts Telegram on failure only (found-0 is a legitimate result, not a failure).
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO/api/venv/bin/python"
DB="${WAREHOUSE_DB:-/data/warehouse.db}"
: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN unset}"
: "${TELEGRAM_CHAT_ID:?TELEGRAM_CHAT_ID unset}"

alert() {
  curl -s -m 10 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d chat_id="${TELEGRAM_CHAT_ID}" --data-urlencode text="open-scholarships discover: $1" >/dev/null || true
}

heartbeat() { # $1 found  $2 errors  $3 exit_code
  sqlite3 "$DB" "CREATE TABLE IF NOT EXISTS scholarship_runs (ts TEXT, script TEXT, found INTEGER, new_records INTEGER, errors TEXT, exit_code INTEGER);
INSERT INTO scholarship_runs VALUES ('$(date -Iseconds)', 'discover', ${1:-0}, ${1:-0}, '$2', $3);" || true
}

cd "$REPO"
if ! git pull --ff-only; then
  heartbeat 0 "git pull failed" 1
  alert "git pull --ff-only failed on $(hostname) — clone wedged, timer run aborted"
  exit 1
fi

OUT="$("$PY" tools/discover.py --max-records 40 2>&1)"
RC=$?
echo "$OUT"
FOUND="$(echo "$OUT" | sed -n 's/^=== \([0-9][0-9]*\) new candidate(s).*/\1/p' | tail -1)"

if [ "$RC" -ne 0 ]; then
  heartbeat "${FOUND:-0}" "discover.py exit $RC" "$RC"
  alert "discover.py failed (exit $RC) on $(hostname)"
  exit "$RC"
fi
if echo "$OUT" | grep -q "git publish skipped/failed"; then
  heartbeat "${FOUND:-0}" "git push failed" 1
  alert "found ${FOUND:-?} record(s) but git push failed — records stranded on the Arch clone"
  exit 1
fi
heartbeat "${FOUND:-0}" "" 0
