#!/bin/bash

set -euo pipefail

# A sync cycle must finish well inside the sleep window. timeout bounds the
# whole cycle (download + import + analyze); a hung run — e.g. the upstream
# headless MFA prompt — fails loudly instead of blocking forever, and the
# distinct exit code feeds the Datadog sync-heartbeat monitor in malazan.xyz.
readonly SYNC_TIMEOUT="${SYNC_TIMEOUT:-3600}"
readonly SLEEP_SECONDS="${SLEEP_SECONDS:-86400}"
readonly SYNC_CMD=(garmindb_cli.py --all --download --import --analyze --latest --simple-output)

while true; do
  echo "$(date -u +"%Y-%m-%dT%H:%M:%SZ") [entrypoint] Starting GarminDB sync"
  set +e
  timeout "${SYNC_TIMEOUT}" "${SYNC_CMD[@]}" 2>&1
  exit_code=$?
  set -e
  if [ "${exit_code}" -eq 0 ]; then
    echo "$(date -u +"%Y-%m-%dT%H:%M:%SZ") [entrypoint] GarminDB sync completed"
  else
    echo "$(date -u +"%Y-%m-%dT%H:%M:%SZ") [entrypoint] GarminDB sync failed with exit code ${exit_code}"
  fi
  sleep "${SLEEP_SECONDS}"
done
