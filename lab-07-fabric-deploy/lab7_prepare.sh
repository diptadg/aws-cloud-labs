#!/usr/bin/env bash
#
# CITS5503 Lab 7 - prepare the local (WSL) environment
# ----------------------------------------------------
# Installs Fabric into the active lab virtual environment and checks the things
# that silently break a Fabric run on WSL2 before they cost you a demo slot:
#
#   * fabric present, and the version reported
#   * private key on the Linux filesystem, not under /mnt/c
#   * private key mode 600 and owned by you
#   * ~/.ssh present with sane permissions
#   * outbound TCP 22 reachable from this network
#
# This script only reads lab.env. It writes nothing to it.
#
#   labenv
#   ./lab7_prepare.sh

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lab7_lib.sh
source "${HERE}/lab7_lib.sh"

lab_env_load
lab_env_require LAB_PREFIX AWS_REGION

# These fallbacks exist only because this script legitimately runs BEFORE
# lab7_ec2.py has written the real values. Every later script requires them.
KEY_NAME="${KEY_NAME:-${LAB_PREFIX}-key}"
KEY_PATH="${KEY_PATH:-${HOME}/.ssh/${KEY_NAME}.pem}"
SSH_USER="${SSH_USER:-ubuntu}"

lab_env_report LAB_PREFIX AWS_REGION KEY_NAME KEY_PATH SSH_USER

fail=0
step() { printf '\n== %s\n' "$1"; }
ok()   { printf '   ok    %s\n' "$1"; }
warn() { printf '   warn  %s\n' "$1"; }
bad()  { printf '   FAIL  %s\n' "$1"; fail=1; }

# ------------------------------------------------------------------- venv
step "Python virtual environment"
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  bad "no virtual environment active. Run 'labenv' first."
  echo
  echo "Ubuntu refuses a bare 'pip install' (PEP 668), so Fabric must go into"
  echo "the shared lab venv rather than the system Python."
  exit 1
fi
ok "using ${VIRTUAL_ENV}"

# ----------------------------------------------------------------- fabric
step "Fabric"
if python3 -c "import fabric" 2>/dev/null; then
  ok "already installed"
else
  echo "   installing..."
  pip install --quiet fabric
fi
FAB_VER="$(python3 -c 'import fabric; print(fabric.__version__)')"
ok "fabric ${FAB_VER}"
ok "invoke  $(python3 -c 'import invoke; print(invoke.__version__)')"
ok "paramiko $(python3 -c 'import paramiko; print(paramiko.__version__)')"

# -------------------------------------------------------------- ~/.ssh dir
step "~/.ssh"
if [[ ! -d "${HOME}/.ssh" ]]; then
  mkdir -p "${HOME}/.ssh"
  ok "created ${HOME}/.ssh"
fi
chmod 700 "${HOME}/.ssh"
ok "${HOME}/.ssh is mode 700"

# ------------------------------------------------------------- private key
step "Private key"
case "${KEY_PATH}" in
  /mnt/[a-z]/*)
    bad "${KEY_PATH} is on the Windows drive"
    echo
    echo "   chmod is a silent no-op on DrvFs, so ssh will keep rejecting the key"
    echo "   as 'too open'. Move it onto the Linux filesystem:"
    echo
    echo "     mkdir -p ~/.ssh"
    echo "     cp '${KEY_PATH}' ~/.ssh/${KEY_NAME}.pem"
    echo "     chmod 600 ~/.ssh/${KEY_NAME}.pem"
    echo
    echo "   then set KEY_PATH in ${LAB_ENV_FILE} to the new path."
    ;;
  *)
    if [[ -f "${KEY_PATH}" ]]; then
      chown "$(id -un):$(id -gn)" "${KEY_PATH}" 2>/dev/null || true
      chmod 600 "${KEY_PATH}"
      MODE="$(stat -c '%a' "${KEY_PATH}")"
      if [[ "${MODE}" == "600" ]]; then
        ok "${KEY_PATH} is mode 600"
      else
        bad "${KEY_PATH} is mode ${MODE} and chmod did not take effect"
      fi
      if head -n1 "${KEY_PATH}" | grep -q 'PRIVATE KEY'; then
        ok "looks like a PEM private key"
      else
        bad "${KEY_PATH} does not start with a PRIVATE KEY header"
      fi
      if grep -q $'\r' "${KEY_PATH}"; then
        bad "${KEY_PATH} contains CRLF line endings - ssh will reject it"
        echo "   fix:  dos2unix '${KEY_PATH}'   (or: sed -i 's/\\r\$//' '${KEY_PATH}')"
      else
        ok "no CRLF line endings"
      fi
    else
      warn "${KEY_PATH} does not exist yet - lab7_ec2.py will create it"
    fi
    ;;
esac

# ------------------------------------------------------------ script files
step "Script line endings"
crlf=0
for f in "${HERE}"/lab7_*.sh "${HERE}"/lab7_*.py; do
  [[ -e "${f}" ]] || continue
  if grep -q $'\r' "${f}"; then
    bad "$(basename "${f}") has CRLF endings - bash will report 'bad interpreter'"
    crlf=1
  fi
done
(( crlf )) && echo "   fix:  dos2unix ${HERE}/lab7_*" || ok "all LF"

# ----------------------------------------------------------------- network
step "Outbound connectivity"
if timeout 8 bash -c "</dev/tcp/github.com/22" 2>/dev/null; then
  ok "TCP 22 outbound is open"
else
  warn "could not open TCP 22 outbound"
  echo "   UWA campus wifi commonly blocks 22 and 80. Fabric will not connect"
  echo "   from here - use a phone hotspot or the VPN for the deploy."
fi

# ------------------------------------------------------------------- clock
step "Clock"
DRIFT="$(AWS_REGION="${AWS_REGION}" python3 - <<'PY'
import datetime, os, subprocess
try:
    endpoint = "https://s3.{}.amazonaws.com".format(os.environ["AWS_REGION"])
    out = subprocess.run(["curl", "-sI", endpoint],
                         capture_output=True, text=True, timeout=10).stdout
    for line in out.splitlines():
        if line.lower().startswith("date:"):
            remote = datetime.datetime.strptime(line[5:].strip(),
                                                "%a, %d %b %Y %H:%M:%S %Z")
            local = datetime.datetime.utcnow()
            print(int(abs((local - remote).total_seconds())))
            break
    else:
        print(-1)
except Exception:
    print(-1)
PY
)"
if [[ "${DRIFT}" == "-1" ]]; then
  warn "could not measure clock drift (no network?)"
elif (( DRIFT > 120 )); then
  bad "local clock is ${DRIFT}s away from AWS - signature errors ahead"
  echo "   fix:  sudo hwclock -s"
else
  ok "clock within ${DRIFT}s of AWS"
fi

echo
if (( fail )); then
  echo "Prepare finished with failures above. Fix them before lab7_ec2.py."
  exit 1
fi
echo "Prepare complete. Next:  python3 lab7_ec2.py"
