#!/usr/bin/env bash
# Detached Slack progress watcher for the career-ops backlog batch.
# Posts progress every 5 min while batch-runner.sh is alive, then a final summary.
# Survives terminal/Claude close (launch with nohup). Reads SLACK_WEBHOOK_URL from .env.
#
#   nohup bash tools/slack_watch.sh >/dev/null 2>&1 &
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
set -a; [ -f .env ] && . ./.env; set +a
[ -z "${SLACK_WEBHOOK_URL:-}" ] && { echo "no SLACK_WEBHOOK_URL"; exit 0; }

TOTAL=$(tail -n +2 batch/batch-input.tsv 2>/dev/null | grep -c '[^[:space:]]')

post() {
  local payload
  payload=$(node -e 'process.stdout.write(JSON.stringify({text:process.argv[1]}))' "$1")
  curl -fsS -X POST -H 'Content-type: application/json' --data "$payload" "$SLACK_WEBHOOK_URL" >/dev/null 2>&1
}
snap() { awk -F'\t' 'NR>1{c[$3]++} END{printf "%d %d %d", c["completed"]+0, c["failed"]+0, c["processing"]+0}' batch/batch-state.tsv; }
running() { pgrep -f "batch/batch-runner.sh" >/dev/null 2>&1; }

while running; do
  sleep 300
  read -r comp fail proc <<< "$(snap)"
  post "⏳ backlog: *$((comp + fail))/${TOTAL}* processed — ✅ ${comp} · ❌ ${fail} · ▶️ ${proc} running"
done

read -r comp fail proc <<< "$(snap)"
slfail=$(awk -F'\t' 'NR>1 && $3=="failed" && $8 ~ /session limit/' batch/batch-state.tsv | wc -l | tr -d ' ')
post "$(printf '🏁 *career-ops backlog ended* — ✅ %s completed · ❌ %s failed (%s quota) · of %s.' "$comp" "$fail" "$slfail" "$TOTAL")"
