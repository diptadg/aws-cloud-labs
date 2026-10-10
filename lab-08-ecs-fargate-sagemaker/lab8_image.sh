#!/usr/bin/env bash
# CITS5503 Lab 8 - build the Jupyter image and test it locally.
#
#   ./lab8_image.sh            build only
#   ./lab8_image.sh --test     build, then run it detached and check the notebook
#   ./lab8_image.sh --slim     build on python:3.10-slim instead (much smaller)
#   ./lab8_image.sh --stop     stop and remove the local test container
#   ./lab8_image.sh --diagnose  work out why the page in your browser is blank
#
# --slim and --test combine:  ./lab8_image.sh --slim --test
#
# Every name, port and token comes from ~/cits5503/lab.env. Run
# ./lab8_prepare.sh first if those keys are not there yet.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lab8_lib.sh
source "${HERE}/lab8_lib.sh"

lab_env_load
# Only what every mode needs. --stop and --diagnose do not build, so build-time
# keys are checked further down, just before the build itself.
lab_env_require LAB_PREFIX LAB8_IMAGE_NAME LAB8_IMAGE_TAG \
                LAB8_JUPYTER_PORT LAB8_JUPYTER_TOKEN LAB8_NOTEBOOK

# Non-identity defaults, so a lab.env seeded by an earlier version of this suite
# still works instead of stopping with a missing-key error.
lab_env_optional LAB8_BASE_IMAGE "python:3.10"
lab_env_optional LAB8_SAGEMAKER_SPEC "sagemaker>=2.200,<3"
lab_env_optional LAB8_NOTEBOOK_URL \
  "https://raw.githubusercontent.com/zhangzhics/CITS5503_Sem2/master/Labs/src/LabAI.ipynb"

IMAGE="${LAB8_IMAGE_NAME}:${LAB8_IMAGE_TAG}"
TEST_CONTAINER="${LAB8_IMAGE_NAME}-local"

do_test=0
do_diagnose=0
base_image="${LAB8_BASE_IMAGE}"

while (( $# )); do
  case "$1" in
    --test) do_test=1 ;;
    --slim) base_image="python:3.10-slim" ;;
    --stop)
      docker rm -f "${TEST_CONTAINER}" >/dev/null 2>&1 \
        && echo "Removed ${TEST_CONTAINER}." \
        || echo "${TEST_CONTAINER} was not running."
      exit 0
      ;;
    --diagnose) do_diagnose=1 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

command -v docker >/dev/null 2>&1 || {
  echo "docker not found. Run ./lab8_prepare.sh for the setup checks." >&2; exit 1; }
docker info >/dev/null 2>&1 || {
  echo "The Docker daemon is not reachable. Start Docker Desktop on Windows." >&2; exit 1; }


# --------------------------------------------------------------- diagnose
# A blank page has four distinct causes and they are easy to tell apart from
# here, but impossible to tell apart from the browser.
if (( do_diagnose )); then
  url="http://127.0.0.1:${LAB8_JUPYTER_PORT}"
  echo "=============================================================="
  echo " Why is ${url} blank?"
  echo "=============================================================="
  echo

  echo "1. Is a container running at all?"
  state="$(docker inspect -f '{{.State.Status}}' "${TEST_CONTAINER}" 2>/dev/null || true)"
  if [[ -z "${state}" ]]; then
    echo "   No container named ${TEST_CONTAINER} exists."
    echo
    echo "   Building the image does not start anything. ./lab8_image.sh on its"
    echo "   own only builds. Start it with:"
    echo "     ./lab8_image.sh --test"
    exit 1
  fi
  echo "   ${TEST_CONTAINER} is ${state}"
  if [[ "${state}" != "running" ]]; then
    echo "   Exit code: $(docker inspect -f '{{.State.ExitCode}}' "${TEST_CONTAINER}")"
    echo
    echo "   Last 30 log lines:"
    docker logs "${TEST_CONTAINER}" 2>&1 | tail -30 | sed 's/^/     /'
    exit 1
  fi
  echo "   Published ports: $(docker port "${TEST_CONTAINER}" 2>/dev/null | tr '\n' ' ')"
  echo

  echo "2. Is Jupyter answering inside WSL?"
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 \
            "${url}/api?token=${LAB8_JUPYTER_TOKEN}" 2>/dev/null || true)"
  if [[ "${code}" == "200" ]]; then
    echo "   yes, HTTP 200 from ${url}/api"
  else
    echo "   no (HTTP '${code:-no response}')"
    echo
    echo "   Jupyter may still be starting - it takes 10-20 seconds. Its own"
    echo "   startup banner prints the URL it is actually serving:"
    echo
    docker logs "${TEST_CONTAINER}" 2>&1 | tail -30 | sed 's/^/     /'
    exit 1
  fi
  echo

  echo "3. Does the file browser have anything in it?"
  listing="$(curl -sS --max-time 5 "${url}/api/contents?token=${LAB8_JUPYTER_TOKEN}" 2>/dev/null || true)"
  if grep -q "${LAB8_NOTEBOOK}" <<<"${listing}"; then
    echo "   yes, ${LAB8_NOTEBOOK} is there"
  else
    echo "   the working directory is EMPTY - this is what a blank file listing means"
    echo
    echo "   Contents of /notebook inside the container:"
    docker exec "${TEST_CONTAINER}" ls -la /notebook 2>&1 | sed 's/^/     /'
    echo
    echo "   If that is empty, the notebook download did not happen during the"
    echo "   build. Rebuild with --no-cache so the ADD step runs again:"
    echo "     docker build --no-cache -t ${IMAGE} ${HERE}"
    exit 1
  fi
  echo

  echo "4. Server is fine, so the blank page is in the browser."
  echo
  echo "   Open this exact address, including http:// -"
  echo "   typing 127.0.0.1:8888 into the address bar makes some browsers search:"
  echo
  echo "     ${url}/tree?token=${LAB8_JUPYTER_TOKEN}"
  echo
  echo "   If it is still white, hard-refresh with Ctrl+Shift+R. A part-loaded"
  echo "   JupyterLab bundle caches as a broken page and a normal reload serves"
  echo "   the same broken cache back. An incognito window rules this out."
  echo
  echo "   For the checkpoint you need the token login screen, so use the bare"
  echo "   ${url} once it works and type ${LAB8_JUPYTER_TOKEN} into the prompt."
  exit 0
fi

lab_env_report LAB8_IMAGE_NAME LAB8_IMAGE_TAG LAB8_JUPYTER_PORT \
               LAB8_JUPYTER_TOKEN LAB8_NOTEBOOK
echo "  Base image      ${base_image}"
echo "  SageMaker SDK   ${LAB8_SAGEMAKER_SPEC}"
echo

# "exporting layers" is the step that stalls when the Docker Desktop VM runs out
# of room, and it stalls silently - no error, just no progress. Check first.
vm_avail="$(docker run --rm -v /:/host alpine df -BG /host 2>/dev/null | tail -1 | awk '{gsub(/G/,"",$4); print $4}')"
if [[ -n "${vm_avail}" ]]; then
  if (( vm_avail < 10 )); then
    echo "[WARN] only ${vm_avail}G free inside the Docker VM."
    echo "       The export step needs room for the whole image at once and will"
    echo "       appear to hang if it runs short. Free space first:"
    echo "         docker system prune -a --volumes"
    echo "       or build smaller with:  ./lab8_image.sh --slim"
    echo
  else
    echo "Docker VM has ${vm_avail}G free."
    echo
  fi
fi

# ------------------------------------------------------------------- build
lab_env_require LAB8_NOTEBOOK_URL

echo "Building ${IMAGE} from ${HERE}/Dockerfile on ${base_image}"
echo "(first build pulls the base image and installs jupyter/sagemaker/awscli)"
echo "(the last step, 'exporting layers', is the slow one - give it several minutes)"
echo
docker build \
  --build-arg "BASE_IMAGE=${base_image}" \
  --build-arg "JUPYTER_TOKEN=${LAB8_JUPYTER_TOKEN}" \
  --build-arg "JUPYTER_PORT=${LAB8_JUPYTER_PORT}" \
  --build-arg "NOTEBOOK_URL=${LAB8_NOTEBOOK_URL}" \
  --build-arg "SAGEMAKER_SPEC=${LAB8_SAGEMAKER_SPEC}" \
  -t "${IMAGE}" \
  "${HERE}"
echo

size="$(docker image inspect "${IMAGE}" --format '{{.Size}}')"
arch="$(docker image inspect "${IMAGE}" --format '{{.Architecture}}')"
os="$(docker image inspect "${IMAGE}" --format '{{.Os}}')"
printf 'Built %s  (%s/%s, %.2f GB)\n\n' "${IMAGE}" "${os}" "${arch}" \
  "$(awk -v s="${size}" 'BEGIN{print s/1073741824}')"

# ECS needs to know the architecture: an ARM64 image will not start on the
# default Fargate x86_64 platform, and vice versa. lab8_ecs.py reads this.
lab_env_set LAB8_IMAGE_ARCH "${arch}" LAB8_BASE_IMAGE "${base_image}"
echo "Recorded LAB8_IMAGE_ARCH=${arch} and LAB8_BASE_IMAGE=${base_image} in ${LAB_ENV_FILE}"
echo

(( do_test )) || { echo "Next:  ./lab8_image.sh --test    (or ./lab8_ecr.py)"; exit 0; }

# -------------------------------------------------------------- local test
echo "Starting a local test container on port ${LAB8_JUPYTER_PORT}"
docker rm -f "${TEST_CONTAINER}" >/dev/null 2>&1 || true
docker run -d --name "${TEST_CONTAINER}" \
  -p "${LAB8_JUPYTER_PORT}:${LAB8_JUPYTER_PORT}" \
  "${IMAGE}" >/dev/null

cleanup_on_fail() {
  echo
  echo "Container log:"
  docker logs "${TEST_CONTAINER}" 2>&1 | tail -30
  echo
  echo "Remove it with:  ./lab8_image.sh --stop"
}

url="http://127.0.0.1:${LAB8_JUPYTER_PORT}"
echo -n "Waiting for Jupyter to answer on ${url} "
for _ in $(seq 1 30); do
  if curl -sS -o /dev/null --max-time 2 "${url}/api?token=${LAB8_JUPYTER_TOKEN}" 2>/dev/null; then
    echo " up"
    break
  fi
  echo -n "."
  sleep 2
done
echo

if ! curl -sS -o /dev/null --max-time 5 "${url}/api?token=${LAB8_JUPYTER_TOKEN}"; then
  echo "[FAIL] Jupyter did not answer."
  cleanup_on_fail
  exit 1
fi
echo "[ ok ] Jupyter is serving"

# The notebook is what the tuning job is run from - confirm the build fetched it.
if docker exec "${TEST_CONTAINER}" test -s "/notebook/${LAB8_NOTEBOOK}"; then
  echo "[ ok ] /notebook/${LAB8_NOTEBOOK} downloaded during the build"
else
  echo "[FAIL] /notebook/${LAB8_NOTEBOOK} is missing or empty."
  cleanup_on_fail
  exit 1
fi

if curl -sS --max-time 5 "${url}/api/contents?token=${LAB8_JUPYTER_TOKEN}" \
     | grep -q "${LAB8_NOTEBOOK}"; then
  echo "[ ok ] the notebook is listed by the running server"
else
  echo "[warn] the contents API did not list ${LAB8_NOTEBOOK} - check in the browser"
fi

echo
echo "Open either of these:"
echo "  ${url}/tree?token=${LAB8_JUPYTER_TOKEN}   straight in"
echo "  ${url}                                    token login form"
echo
echo "The checkpoint asks you to log in with the token, so use the second one"
echo "on the day and type ${LAB8_JUPYTER_TOKEN} at the prompt."
echo
echo "If the browser shows a blank page:  ./lab8_image.sh --diagnose"
echo "Stop it when you are done:          ./lab8_image.sh --stop"
echo "Next:  python3 lab8_ecr.py"
