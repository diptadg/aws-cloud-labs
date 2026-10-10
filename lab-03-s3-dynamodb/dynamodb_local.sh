#!/usr/bin/env bash
# =============================================================================
# CITS5503 Lab 3 - DynamoDB Local (Docker route)
#
# The lab sheet offers two routes: a JRE + downloaded tarball, or Docker.
# Docker Desktop already gives us the CLI through WSL integration, so the
# Docker route is used here - it avoids installing default-jre and avoids
# tying up a terminal window with a foreground java process.
#
# Usage: ./dynamodb_local.sh {start|stop|status|logs|fixperms}
# =============================================================================
set -euo pipefail

CONTAINER="${DDB_CONTAINER:-dynamodb-local}"
IMAGE="${DDB_IMAGE:-amazon/dynamodb-local}"
PORT="${DDB_PORT:-8000}"
ACTION="${1:-start}"

require_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker not found. Start Docker Desktop on Windows and make sure"
    echo "       WSL integration is enabled for this distro."
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "ERROR: docker daemon not reachable. Start Docker Desktop, then retry."
    exit 1
  fi
}

case "${ACTION}" in
  start)
    require_docker

    if docker ps --format '{{.Names}}' | grep -qx "${CONTAINER}"; then
      echo "${CONTAINER} is already running on port ${PORT}"
      exit 0
    fi

    # Remove a stopped container of the same name so this stays idempotent.
    if docker ps -a --format '{{.Names}}' | grep -qx "${CONTAINER}"; then
      echo "Removing stopped container ${CONTAINER} ..."
      docker rm "${CONTAINER}" >/dev/null
    fi

    # If something else already holds the port, bail with a clear message
    # rather than letting docker throw a wall of text.
    if ss -ltn 2>/dev/null | grep -q ":${PORT} "; then
      echo "ERROR: port ${PORT} is already in use. Either stop that process or run:"
      echo "       DDB_PORT=8001 ./dynamodb_local.sh start"
      exit 1
    fi

    echo "Starting DynamoDB Local on http://localhost:${PORT} ..."
    docker run -d -p "${PORT}:8000" --name "${CONTAINER}" "${IMAGE}" \
      -jar DynamoDBLocal.jar -inMemory -sharedDb >/dev/null

    # Wait for it to answer before returning
    for _ in $(seq 1 20); do
      if aws dynamodb list-tables --endpoint-url "http://localhost:${PORT}" >/dev/null 2>&1; then
        echo "DynamoDB Local is up."
        exit 0
      fi
      sleep 0.5
    done
    echo "WARNING: container started but did not respond yet. Check: $0 logs"
    ;;

  fixperms)
    # Only relevant if you swapped -inMemory for -dbPath and bind-mounted a
    # host directory: the container writes shared-local-instance.db as its own
    # UID, which leaves a root-owned file sitting in your lab directory.
    DB_FILE=$(find . -maxdepth 3 -name 'shared-local-instance.db' 2>/dev/null | head -n1)
    if [[ -n "${DB_FILE}" ]]; then
      sudo chown "${USER}:${USER}" "${DB_FILE}"
      chmod 644 "${DB_FILE}"
      echo "chown ${USER}:${USER} ${DB_FILE}"
    else
      echo "No shared-local-instance.db found - nothing to fix (-inMemory keeps no files)."
    fi
    ;;

  stop)
    require_docker
    docker stop "${CONTAINER}" >/dev/null 2>&1 || true
    docker rm "${CONTAINER}" >/dev/null 2>&1 || true
    echo "${CONTAINER} stopped and removed."
    ;;

  status)
    require_docker
    docker ps -a --filter "name=${CONTAINER}" \
      --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
    echo
    echo "Tables currently on localhost:${PORT}:"
    aws dynamodb list-tables --endpoint-url "http://localhost:${PORT}" --output table \
      || echo "  (not reachable)"
    ;;

  logs)
    require_docker
    docker logs "${CONTAINER}"
    ;;

  *)
    echo "Usage: $0 {start|stop|status|logs|fixperms}"
    exit 1
    ;;
esac
