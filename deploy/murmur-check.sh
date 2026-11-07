#!/usr/bin/env bash
# Murmur watcher on gabe-server: read the API's health report and notify through
# ntfy-send (the homelab's only notification path; the containers hold no token).
# Install: /home/gabe/bin/murmur-check.sh + deploy/murmur-check.{service,timer} as user
# units (systemctl --user enable --now murmur-check.timer). Runs at 07:00 ET (after
# the 04:30 snapshot and its upload) and 20:15 ET (after the 18:00 run and retries).
# Test without notifying: MURMUR_CHECK_DRY=1 murmur-check.sh
set -euo pipefail

URL="${MURMUR_REPORT_URL:-http://127.0.0.1:8100/api/v1/health/report}"
CLICK="${MURMUR_CLICK_URL:-https://murmur.gabe.beer/admin}"
NTFY="${NTFY_SEND:-/usr/local/bin/ntfy-send}"

notify() {  # priority title message
  if [ -n "${MURMUR_CHECK_DRY:-}" ]; then
    printf 'would send [%s] %s\n%s\n' "$1" "$2" "$3"
  else
    "$NTFY" -t chart_with_downwards_trend --click "$CLICK" "$1" "$2" "$3"
  fi
}

if ! report="$(curl -fsS --max-time 20 "$URL")"; then
  notify high "Murmur is not answering" "GET $URL failed. docker compose -f ~/murmur/docker-compose.yml ps"
  exit 1
fi

summary="$(printf '%s' "$report" | python3 -c '
import json, sys
r = json.load(sys.stdin)
if r.get("ok"):
    sys.exit(0)
lines = ["- " + p for p in r.get("problems", [])]
lines += ["(" + w + ")" for w in r.get("warnings", [])]
print("\n".join(lines))
sys.exit(3)
')" && status=0 || status=$?

if [ "$status" = 3 ]; then
  count="$(printf '%s\n' "$summary" | grep -c '^- ' || true)"
  notify high "Murmur: $count problem(s)" "$summary"
  exit 1
elif [ "$status" != 0 ]; then
  notify high "Murmur check failed" "could not parse the health report"
  exit 1
fi
echo "$(date -Is) ok"
