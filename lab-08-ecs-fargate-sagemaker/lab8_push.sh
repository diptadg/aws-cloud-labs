#!/usr/bin/env bash
# CITS5503 Lab 8 - log in to ECR, tag the local image and push it.
#
#   ./lab8_push.sh              log in, tag, push
#   ./lab8_push.sh --show-cmd   print the login command from the lab sheet and stop
#
# The lab sheet has you print `docker login -u AWS -p <password> <registry>` and
# paste it into the terminal. That works, but the password ends up in your shell
# history and in the process list, so this script feeds the same password to
# `docker login --password-stdin` instead. --show-cmd still prints the lab
# sheet's version if you want to run it by hand.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lab8_lib.sh
source "${HERE}/lab8_lib.sh"

lab_env_load
lab_env_require LAB8_IMAGE_NAME LAB8_IMAGE_TAG LAB8_ECR_REPO \
                LAB8_ECR_URI LAB8_ECR_REGISTRY

if [[ "${1:-}" == "--show-cmd" ]]; then
  python3 "${HERE}/lab8_ecr.py" --login-cmd
  exit 0
fi

LOCAL="${LAB8_IMAGE_NAME}:${LAB8_IMAGE_TAG}"
REMOTE="${LAB8_ECR_URI}:${LAB8_IMAGE_TAG}"

command -v docker >/dev/null 2>&1 || { echo "docker not found." >&2; exit 1; }
docker info >/dev/null 2>&1 || {
  echo "The Docker daemon is not reachable. Start Docker Desktop on Windows." >&2; exit 1; }

if ! docker image inspect "${LOCAL}" >/dev/null 2>&1; then
  echo "Local image ${LOCAL} not found. Build it first:  ./lab8_image.sh" >&2
  exit 1
fi

lab_env_report LAB8_IMAGE_NAME LAB8_IMAGE_TAG LAB8_ECR_URI

# ------------------------------------------------------------------- login
echo "Logging in to ${LAB8_ECR_REGISTRY}"
if ! python3 "${HERE}/lab8_ecr.py" --password \
     | docker login -u AWS --password-stdin "${LAB8_ECR_REGISTRY}"; then
  echo >&2
  echo "Login failed." >&2
  echo "If the error says 'no such host', WSL2's DNS is broken:" >&2
  echo "  sudo rm /etc/resolv.conf" >&2
  echo '  echo "nameserver 8.8.8.8" | sudo tee /etc/resolv.conf' >&2
  echo "  sudo chattr +i /etc/resolv.conf" >&2
  echo "then restart WSL2 from PowerShell:  wsl --shutdown" >&2
  exit 1
fi
echo

# ------------------------------------------------------------- tag and push
echo "Tagging ${LOCAL} as ${REMOTE}"
docker tag "${LOCAL}" "${REMOTE}"

echo "Pushing ${REMOTE}"
echo "(roughly 2 GB of layers - this is the slow step, and it depends on your uplink)"
echo
docker push "${REMOTE}"
echo

digest="$(docker image inspect "${REMOTE}" --format '{{index .RepoDigests 0}}' 2>/dev/null || true)"
[[ -n "${digest}" ]] && echo "Pushed digest: ${digest}"

lab_env_set LAB8_IMAGE_URI "${REMOTE}"
echo "Recorded LAB8_IMAGE_URI=${REMOTE} in ${LAB_ENV_FILE}"
echo
echo "Confirm in the console: ECR -> Repositories -> ${LAB8_ECR_REPO} -> Images"
echo "Next:  python3 lab8_ecs.py"
