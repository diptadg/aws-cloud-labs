#!/usr/bin/env bash
# =============================================================================
# CITS5503 Lab 3 - Scan the CloudFiles table with the AWS CLI
#
# This is the exact command used for Checkpoint 2. It is a live scan against
# DynamoDB Local, so the container must still be running.
# =============================================================================
set -euo pipefail

PORT="${DDB_PORT:-8000}"
ENDPOINT="http://localhost:${PORT}"
TABLE="${DDB_TABLE:-CloudFiles}"

if ! aws dynamodb list-tables --endpoint-url "${ENDPOINT}" >/dev/null 2>&1; then
  echo "ERROR: DynamoDB Local is not answering on ${ENDPOINT}."
  echo "       ./dynamodb_local.sh start"
  exit 1
fi

aws dynamodb scan \
  --table-name "${TABLE}" \
  --endpoint-url "${ENDPOINT}" \
  --output table
