#!/usr/bin/env sh
# Trigger the collect workflow from an external scheduler (cron-job.org, a VPS
# crontab, anything that can run a command or make an HTTPS request).
#
# This exists because GitHub's own `schedule` trigger is best-effort: on this
# repo it has been observed firing every 4-8 hours instead of hourly, which
# leaves holes in data/raw. An outside scheduler calling workflow_dispatch is
# the only way to get a predictable cadence.
#
# The token comes from the environment only - never hardcode it, never commit it:
#   PADEL_DISPATCH_TOKEN  fine-grained PAT, "Actions: read and write" on this repo
#
# Optional overrides:
#   PADEL_REPO      default Nikita1129/Padel
#   PADEL_WORKFLOW  default collect.yml
#   PADEL_REF       default main
#
# Exit code 0 only when GitHub accepted the dispatch (HTTP 204).
set -eu

REPO="${PADEL_REPO:-Nikita1129/Padel}"
WORKFLOW="${PADEL_WORKFLOW:-collect.yml}"
REF="${PADEL_REF:-main}"

if [ -z "${PADEL_DISPATCH_TOKEN:-}" ]; then
    echo "ERROR: PADEL_DISPATCH_TOKEN is not set" >&2
    exit 2
fi

# dry_run=false       -> write the CSV and push to the Sheet
# ignore_window=false -> keep the 06:30-23:59 Chisinau gate, so night runs exit 0 early
# commit_html=false   -> do not commit fetched pages into fixtures/real/
body='{"ref":"'"$REF"'","inputs":{"dry_run":"false","ignore_window":"false","commit_html":"false"}}'

out="$(mktemp)"
trap 'rm -f "$out"' EXIT

status="$(
    curl -sS -o "$out" -w '%{http_code}' -X POST \
        -H "Accept: application/vnd.github+json" \
        -H "Authorization: Bearer $PADEL_DISPATCH_TOKEN" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        -H "Content-Type: application/json" \
        -d "$body" \
        "https://api.github.com/repos/$REPO/actions/workflows/$WORKFLOW/dispatches"
)"

if [ "$status" = "204" ]; then
    echo "dispatched $WORKFLOW on $REPO@$REF"
    exit 0
fi

echo "ERROR: GitHub returned HTTP $status" >&2
cat "$out" >&2
echo >&2
exit 1
