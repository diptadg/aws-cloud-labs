#!/usr/bin/env bash
# =============================================================================
# CITS5503 Lab 3 - Delete the CloudFiles table with the AWS CLI
#
# This is step [6] of the lab. Run it AFTER the live scan has been shown,
# because the scan needs the table to still exist.
#
# The S3 bucket is deliberately NOT touched here - bucket deletion has to be
# done through the AWS Management Console (see README).
# =============================================================================
set -euo pipefail

PORT="${DDB_PORT:-8000}"
ENDPOINT="http://localhost:${PORT}"
TABLE="${DDB_TABLE:-CloudFiles}"

if [[ "${1:-}" != "--yes" ]]; then
  read -r -p "Delete table ${TABLE} on ${ENDPOINT}? [y/N] " reply
  [[ "${reply}" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
fi

aws dynamodb delete-table \
  --table-name "${TABLE}" \
  --endpoint-url "${ENDPOINT}" \
  --output table

echo
echo "Remaining tables:"
aws dynamodb list-tables --endpoint-url "${ENDPOINT}" --output table
