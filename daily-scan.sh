#!/usr/bin/env bash
# career-ops daily job — scan + evaluate new offers
# Runs at 4am via launchd. Logs to logs/daily-scan.log

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/daily-scan-$(date +%Y-%m-%d).log"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

# Post a run summary to Slack if SLACK_WEBHOOK_URL is set (env or .env). No-op otherwise.
notify_slack() {
  [[ -f "$SCRIPT_DIR/.env" ]] && { set -a; . "$SCRIPT_DIR/.env"; set +a; }
  [[ -z "${SLACK_WEBHOOK_URL:-}" ]] && { log "Slack: SLACK_WEBHOOK_URL not set, skipping notification."; return 0; }

  local state="$SCRIPT_DIR/batch/batch-state.tsv"
  local total=0 completed=0 failed=0 top=""
  if [[ -f "$state" ]]; then
    total=$(awk -F'\t' 'NR>1 && NF>1' "$state" | wc -l | tr -d ' ')
    completed=$(awk -F'\t' 'NR>1 && $3=="completed"' "$state" | wc -l | tr -d ' ')
    failed=$(awk -F'\t' 'NR>1 && $3=="failed"' "$state" | wc -l | tr -d ' ')
    top=$(awk -F'\t' 'NR>1 && $3=="completed" && $7!="-" && $7!="" {print $7"\t"$2}' "$state" \
          | sort -rn | head -5 | awk -F'\t' '{printf "  • %s — %s\n", $1, $2}')
  fi

  local msg
  msg=$(printf '🔎 *career-ops daily scan* — %s\nCompleted: %s | Failed: %s | Total tracked: %s\n\nTop offers this run:\n%s' \
        "$(date '+%Y-%m-%d %H:%M')" "$completed" "$failed" "$total" "${top:-  (none scored)}")

  local payload
  payload=$(node -e 'process.stdout.write(JSON.stringify({text:process.argv[1]}))' "$msg")
  if curl -fsS -X POST -H 'Content-type: application/json' --data "$payload" "$SLACK_WEBHOOK_URL" >/dev/null 2>&1; then
    log "Slack: notification sent."
  else
    log "WARN: Slack notification failed (check SLACK_WEBHOOK_URL)."
  fi
}

log "=== career-ops daily scan starting ==="

# Step 1: scan portals for new offers
log "Running portal scan..."
node scan.mjs >> "$LOG" 2>&1 || { log "WARN: scan.mjs exited with errors (partial results may exist)"; }

# Step 2: convert new pipeline.md entries to batch-input.tsv
log "Syncing pipeline.md → batch-input.tsv..."

# get current max ID in batch-input.tsv
MAX_ID=$(tail -n +2 batch/batch-input.tsv 2>/dev/null | awk -F'\t' 'NF>0 && $1~/^[0-9]+$/ {print $1+0}' | sort -n | tail -1)
MAX_ID=${MAX_ID:-0}

# collect URLs already in batch-input.tsv to avoid duplicates
EXISTING_URLS=$(awk -F'\t' 'NR>1 {print $2}' batch/batch-input.tsv 2>/dev/null || true)

# create header if batch-input.tsv is missing
if [[ ! -f batch/batch-input.tsv ]]; then
  printf 'id\turl\tsource\tnotes\n' > batch/batch-input.tsv
fi

NEW_COUNT=0
while IFS= read -r line; do
  # match pending pipeline entries: - [ ] URL | Company | Title
  if [[ "$line" =~ ^-\ \[\ \]\ (.+)\ \|\ (.+)\ \|\ (.+)$ ]]; then
    url="${BASH_REMATCH[1]}"
    company="${BASH_REMATCH[2]}"
    title="${BASH_REMATCH[3]}"
    # skip if already in batch-input
    if echo "$EXISTING_URLS" | grep -qF "$url"; then
      continue
    fi
    MAX_ID=$((MAX_ID + 1))
    printf '%s\t%s\t%s\t%s\n' "$MAX_ID" "$url" "$company" "$title" >> batch/batch-input.tsv
    NEW_COUNT=$((NEW_COUNT + 1))
  fi
done < data/pipeline.md

log "Added $NEW_COUNT new offers to batch-input.tsv (total IDs up to $MAX_ID)"

# Step 3: run batch evaluation on all pending offers
if [[ $MAX_ID -gt 0 ]]; then
  log "Starting batch evaluation (--parallel 3)..."
  bash batch/batch-runner.sh --parallel 3 >> "$LOG" 2>&1 || log "WARN: batch runner exited with errors (check batch-state.tsv)"
else
  log "No new offers to evaluate."
fi

# Step 4: merge tracker additions into applications.md
log "Merging tracker..."
node merge-tracker.mjs >> "$LOG" 2>&1 || log "WARN: merge-tracker.mjs failed"

# Step 4b: refresh Excel tracker + base resume .docx (auto-update)
if [[ -x .venv/bin/python ]]; then
  log "Refreshing resume .docx + Excel tracker..."
  .venv/bin/python tools/generate_docx.py >> "$LOG" 2>&1 || log "WARN: generate_docx.py failed"
  .venv/bin/python tools/make_tracker_xlsx.py >> "$LOG" 2>&1 || log "WARN: make_tracker_xlsx.py failed"
else
  log "WARN: .venv missing — skipping xlsx/docx refresh (run: python3 -m venv .venv && .venv/bin/pip install openpyxl python-docx)"
fi

# Step 5: notify Slack with a run summary
notify_slack

log "=== Done. Check reports/ and output/ for results. ==="

# clean up old logs (keep 14 days)
find "$LOG_DIR" -name "daily-scan-*.log" -mtime +14 -delete 2>/dev/null || true
