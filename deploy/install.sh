#!/usr/bin/env bash
# Run ON ARCH from the repo root: sudo deploy/install.sh
# Idempotent — re-run after editing any unit or wrapper.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f /etc/open-scholarships.env ]; then
  echo "Missing /etc/open-scholarships.env — create it first (chmod 600):"
  echo "  TELEGRAM_BOT_TOKEN=..."
  echo "  TELEGRAM_CHAT_ID=..."
  echo "  ANTHROPIC_API_KEY=..."
  exit 1
fi

cp scholarship-discover.service scholarship-discover.timer \
   scholarship-freshness.service scholarship-freshness.timer \
   scholarship-curated-reminder.service scholarship-curated-reminder.timer \
   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now scholarship-discover.timer scholarship-freshness.timer scholarship-curated-reminder.timer
systemctl list-timers 'scholarship-*' --no-pager
