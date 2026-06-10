#!/usr/bin/env bash
# Weekly freshness wrapper (Arch timer). check_freshness.py --write flips stale actives to
# needs-review but doesn't touch git — this wrapper commits+pushes the flips, writes the
# warehouse heartbeat, and alerts Telegram on failure only.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO/api/venv/bin/python"
DB="${WAREHOUSE_DB:-/data/warehouse.db}"
: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN unset}"
: "${TELEGRAM_CHAT_ID:?TELEGRAM_CHAT_ID unset}"

alert() {
  curl -s -m 10 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d chat_id="${TELEGRAM_CHAT_ID}" --data-urlencode text="open-scholarships freshness: $1" >/dev/null || true
}

heartbeat() { # $1 found  $2 flipped  $3 errors  $4 exit_code
  sqlite3 "$DB" "CREATE TABLE IF NOT EXISTS scholarship_runs (ts TEXT, script TEXT, found INTEGER, new_records INTEGER, errors TEXT, exit_code INTEGER);
INSERT INTO scholarship_runs VALUES ('$(date -Iseconds)', 'freshness', ${1:-0}, ${2:-0}, '$3', $4);" || true
}

cd "$REPO"
if ! git pull --ff-only; then
  heartbeat 0 0 "git pull failed" 1
  alert "git pull --ff-only failed on $(hostname) — clone wedged, timer run aborted"
  exit 1
fi

OUT="$("$PY" tools/check_freshness.py --write 2>&1)"
RC=$?
echo "$OUT"
ISSUES="$(echo "$OUT" | sed -n 's/^\([0-9][0-9]*\) record(s) need attention.*/\1/p' | tail -1)"
FLIPPED="$(echo "$OUT" | grep -c '^    -> set ')"

if [ "$RC" -ne 0 ]; then
  heartbeat "${ISSUES:-0}" "$FLIPPED" "check_freshness.py exit $RC" "$RC"
  alert "check_freshness.py failed (exit $RC) on $(hostname)"
  exit "$RC"
fi

if [ "$FLIPPED" -gt 0 ]; then
  if ! (git add data &&
        git commit -m "freshness: flag $FLIPPED stale record(s) for re-review" &&
        git pull --rebase origin main &&
        git push origin main); then
    heartbeat "${ISSUES:-0}" "$FLIPPED" "git push failed" 1
    alert "flagged $FLIPPED stale record(s) but git push failed — flips stranded on the Arch clone"
    exit 1
  fi
fi
heartbeat "${ISSUES:-0}" "$FLIPPED" "" 0
