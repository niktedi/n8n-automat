#!/usr/bin/env bash
# Apply the scenario 03 BigQuery schema and demo seed to one dataset.
#
#   ./apply.sh fx_dev     # dev
#   ./apply.sh fx         # prod
#
# Safe to re-run: schema.sql is idempotent, seed.sql truncates and reloads positions.
set -euo pipefail

PROJECT="${PROJECT:-n8n-automat-fx}"
DATASET="${1:-}"

case "$DATASET" in
  fx|fx_dev) ;;
  *) echo "usage: $0 fx_dev|fx" >&2; exit 2 ;;
esac

DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> schema -> $PROJECT:$DATASET"
sed "s/__BQ_DATASET__/${DATASET}/g" "$DIR/schema.sql" \
  | bq query --project_id="$PROJECT" --location=EU --use_legacy_sql=false

echo "==> seed -> $PROJECT:$DATASET.positions"
bq query --project_id="$PROJECT" --location=EU --use_legacy_sql=false \
  --dataset_id="$PROJECT:$DATASET" < "$DIR/seed.sql"

echo "==> objects in $PROJECT:$DATASET"
bq ls "$PROJECT:$DATASET"
