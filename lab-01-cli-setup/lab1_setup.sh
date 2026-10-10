#!/usr/bin/env bash
#
# CITS5503 Lab 1 - Introduction & Setup
# Target environment: Windows 11 host + WSL2 + Ubuntu 26.04
#
# Usage:
#   chmod +x lab1_setup.sh
#   ./lab1_setup.sh                 # full setup (prompts for prefix, region, AMI + keys)
#   ./lab1_setup.sh --reconfigure   # re-run `aws configure` even if already set
#   ./lab1_setup.sh --skip-upgrade  # skip `apt upgrade` (much faster re-runs)
#
set -euo pipefail

LAB_DIR="${LAB_DIR:-$HOME/cits5503}"
VENV_DIR="$LAB_DIR/.venv"
LAB_ENV="${LAB_ENV:-$LAB_DIR/lab.env}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RECONFIGURE=0
SKIP_UPGRADE=0
for arg in "$@"; do
  case "$arg" in
    --reconfigure)  RECONFIGURE=1 ;;
    --skip-upgrade) SKIP_UPGRADE=1 ;;
    -h|--help)      sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

# ---------------------------------------------------------------- helpers ---
log()  { printf '\n\033[1;34m==>\033[0m \033[1m%s\033[0m\n' "$*"; }
ok()   { printf '    \033[1;32m[ok]\033[0m %s\n' "$*"; }
warn() { printf '    \033[1;33m[warn]\033[0m %s\n' "$*"; }
die()  { printf '\n\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

# ------------------------------------------------------------ [0] sanity ----
log "[0] Checking environment"

grep -qi microsoft /proc/version 2>/dev/null \
  && ok "Running inside WSL" \
  || warn "This does not look like WSL — continuing anyway"

if [[ -r /etc/os-release ]]; then
  . /etc/os-release
  ok "Distro: ${PRETTY_NAME:-unknown}"
fi
ok "Architecture: $(uname -m)"

# WSL2 clocks drift after the Windows host sleeps, which makes every AWS call
# fail with InvalidSignatureException. Cheap to check, painful to debug live.
if command -v ntpdate >/dev/null 2>&1 || [[ -e /dev/rtc ]]; then
  if ! sudo hwclock -s 2>/dev/null; then
    warn "Could not sync clock from hardware. If AWS calls fail with"
    warn "'Signature expired', run: sudo hwclock -s"
  else
    ok "System clock synced (guards against signature errors)"
  fi
fi

# Running the whole script as root puts the venv and ~/.aws in root's account,
# which then fails for your normal user during the live assessment.
if [[ $EUID -eq 0 ]]; then
  die "Do not run this script with sudo.
 Run it as your normal user — it calls sudo itself only where needed:
     ./lab1_setup.sh"
fi

mkdir -p "$LAB_DIR" 2>/dev/null || true
if [[ ! -d "$LAB_DIR" ]]; then
  die "Could not create $LAB_DIR — check permissions on $HOME"
fi
if [[ ! -w "$LAB_DIR" ]]; then
  die "$LAB_DIR is not writable by $USER (owned by $(stat -c '%U:%G' "$LAB_DIR")).
 This usually means it was created with sudo. Fix it with:
     sudo chown -R \"\$USER:\$USER\" $LAB_DIR
 Also check ~/.aws, which may have the same problem:
     ls -ld ~/.aws && sudo chown -R \"\$USER:\$USER\" ~/.aws"
fi
ok "Lab directory: $LAB_DIR (writable)"

# ------------------------------------------------------ [1] apt packages ----
log "[1] Installing base packages"

sudo apt-get update -qq
if (( SKIP_UPGRADE == 0 )); then
  sudo DEBIAN_FRONTEND=noninteractive apt-get -y upgrade
fi
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 python3-pip python3-venv curl unzip less groff ca-certificates

ok "Python: $(python3 -V)"
# Ubuntu 26.04 ships Python 3.13+, not the 3.8/3.10 the lab sheet suggests.
# boto3 and the AWS CLI both work fine on it — the recommendation is a floor,
# not a ceiling.

# -------------------------------------------------------- [2] AWS CLI v2 ----
log "[2] Installing AWS CLI v2"

install_awscli_v2() {
  local arch url tmp
  case "$(uname -m)" in
    x86_64)          arch=x86_64 ;;
    aarch64|arm64)   arch=aarch64 ;;
    *) die "Unsupported architecture: $(uname -m)" ;;
  esac
  url="https://awscli.amazonaws.com/awscli-exe-linux-${arch}.zip"
  tmp="$(mktemp -d)"
  echo "    Downloading $url"
  curl -fsSL "$url" -o "$tmp/awscliv2.zip"
  unzip -q "$tmp/awscliv2.zip" -d "$tmp"
  sudo "$tmp/aws/install" --update
  rm -rf "$tmp"
  hash -r
}

if command -v aws >/dev/null 2>&1 && aws --version 2>&1 | grep -q 'aws-cli/2'; then
  ok "Already installed: $(aws --version 2>&1)"
else
  # Do NOT use `apt install awscli` here: on Ubuntu it pulls AWS CLI v1, which
  # is in maintenance mode and behaves differently for several commands.
  install_awscli_v2
  ok "$(aws --version 2>&1)"
fi

# ------------------------------------------ [3] Python venv + boto3 ---------
log "[3] Creating Python virtual environment and installing boto3"

# Ubuntu 26.04 marks the system Python as "externally managed" (PEP 668), so a
# bare `pip3 install boto3` is refused. A venv is the correct fix — never use
# --break-system-packages on the system interpreter.
if [[ -e "$VENV_DIR" && ! -f "$VENV_DIR/bin/activate" ]]; then
  warn "$VENV_DIR exists but looks incomplete (no bin/activate) — rebuilding"
  rm -rf "$VENV_DIR" 2>/dev/null || die \
    "Could not remove $VENV_DIR (owned by $(stat -c '%U:%G' "$VENV_DIR")).
 Remove it manually and re-run:
     sudo rm -rf $VENV_DIR"
fi

if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
  ok "Created venv at $VENV_DIR"
else
  ok "Reusing venv at $VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet --upgrade boto3
ok "boto3 $(python -c 'import boto3; print(boto3.__version__)')"

# Convenience: `labenv` activates the venv from any shell. Named generically
# because every later lab in the unit uses this same environment.
BASHRC_MARK="# >>> cits5503 lab venv >>>"
if ! grep -qF "$BASHRC_MARK" "$HOME/.bashrc" 2>/dev/null; then
  {
    echo ""
    echo "$BASHRC_MARK"
    echo "alias labenv='source $VENV_DIR/bin/activate'"
    echo "# <<< cits5503 lab venv <<<"
  } >> "$HOME/.bashrc"
  ok "Added 'labenv' alias to ~/.bashrc (activates the venv)"
fi

# ---------------------------------------------------- [4] aws configure -----
log "[4] Configuring AWS credentials and region"

# Values already in lab.env (see config.example.env) are offered as defaults,
# so a re-run only needs Enter at each prompt.
if [[ -f "$LAB_ENV" ]]; then
  # shellcheck disable=SC1090
  source "$LAB_ENV"
fi

# Every resource the later labs create is named <prefix>-something. S3 bucket
# names are global, and the longest derived name (a SageMaker tuning job) caps
# the prefix at 9 characters.
read -r -p "    Resource-name prefix [${LAB_PREFIX:-}]: " reply
PREFIX="${reply:-${LAB_PREFIX:-}}"
[[ "$PREFIX" =~ ^[a-z0-9][a-z0-9-]{1,8}$ ]] \
  || die "Prefix must be 2-9 characters: lowercase letters, digits and hyphens."

read -r -p "    AWS region [${AWS_REGION:-ap-southeast-2}]: " reply
REGION="${reply:-${AWS_REGION:-ap-southeast-2}}"

read -r -p "    Ubuntu AMI id in $REGION [${AWS_AMI:-}]: " reply
AMI="${reply:-${AWS_AMI:-}}"
[[ "$AMI" =~ ^ami-[0-9a-f]{8,17}$ ]] || die "AMI id must look like ami-xxxxxxxxxxxxxxxxx (8 or 17 hex digits)."

ok "Prefix:  $PREFIX"
ok "Region:  $REGION"
ok "AMI id:  $AMI   (used from Lab 2 onwards)"

CURRENT_KEY="$(aws configure get aws_access_key_id 2>/dev/null || true)"
if [[ -n "$CURRENT_KEY" && $RECONFIGURE -eq 0 ]]; then
  ok "Credentials already configured (…${CURRENT_KEY: -4}); re-run with --reconfigure to replace"
else
  echo "    Get these from IAM > your user > Security credentials > Create access key"
  echo "    (use case: Command Line Interface)."
  read -r  -p "    AWS Access Key ID:     " AWS_KEY
  read -r -s -p "    AWS Secret Access Key: " AWS_SECRET; echo
  [[ -n "$AWS_KEY" && -n "$AWS_SECRET" ]] || die "Both key and secret are required."
  aws configure set aws_access_key_id     "$AWS_KEY"
  aws configure set aws_secret_access_key "$AWS_SECRET"
  ok "Credentials written to ~/.aws/credentials"
fi

aws configure set region "$REGION"
aws configure set output json
chmod 600 "$HOME/.aws/credentials" 2>/dev/null || true
ok "Region set to $REGION, output format json"

# Persist the prefix / region / AMI for the later lab scripts.
# Update in place: later labs append their own resource ids to this file, and
# blindly rewriting it would throw those away.
#
# The filter also drops the older AWS_LAB_REGION / AWS_LAB_AMI names and any
# duplicate AWS_REGION written by a later lab, so re-running this script
# migrates an existing file instead of leaving two entries for the same value.
if [[ -f "$LAB_ENV" ]]; then
  cp "$LAB_ENV" "$LAB_ENV.bak"
  PRESERVED=$(grep -vE '^(# Sourced by|LAB_PREFIX=|AWS_REGION=|AWS_AMI=|AWS_LAB_REGION=|AWS_LAB_AMI=)' "$LAB_ENV" || true)
else
  PRESERVED=""
fi

{
  echo "# Sourced by later lab scripts: source ~/cits5503/lab.env"
  echo "LAB_PREFIX=\"$PREFIX\""
  echo "AWS_REGION=\"$REGION\""
  echo "AWS_AMI=\"$AMI\""
  [[ -n "$PRESERVED" ]] && printf '%s\n' "$PRESERVED"
} > "$LAB_ENV"

if [[ -n "$PRESERVED" ]]; then
  ok "Updated $LAB_ENV (kept $(wc -l <<<"$PRESERVED") existing line(s); backup at lab.env.bak)"
else
  ok "Saved $LAB_ENV"
fi

# ------------------------------------------------ [5] check lab scripts -----
log "[5] Checking lab scripts"

# No copying: the scripts run fine from wherever you keep them (e.g.
# ~/cits5503/lab1/). Only .venv and lab.env live in $LAB_DIR, and both are
# found by absolute path, so scattering duplicate copies into the parent
# directory would just create two versions to keep in sync.
for f in regions_table.py lab1_verify.sh; do
  if [[ -f "$SCRIPT_DIR/$f" ]]; then
    chmod +x "$SCRIPT_DIR/$f" 2>/dev/null || true
    ok "$f found in $SCRIPT_DIR"
  else
    warn "$f is not in $SCRIPT_DIR — keep all Lab 1 scripts in one directory"
  fi
done

# ------------------------------------------------------- [6] smoke test ----
log "[6] Verifying the environment"

echo "    aws ec2 describe-regions --output table  (first few rows)"
aws ec2 describe-regions --output table | head -n 8

echo
python - <<'PY'
import boto3
ec2 = boto3.client("ec2")
n = len(ec2.describe_regions()["Regions"])
print(f"    boto3 reached EC2: {n} enabled regions returned")
PY

log "Setup complete"
cat <<EOF

  Scripts live in    : $SCRIPT_DIR
  Shared venv        : $VENV_DIR
  Shared config      : $LAB_ENV
  Activate the venv  : source $VENV_DIR/bin/activate     (or just: labenv)

  Checkpoint 3 demo  : cd $SCRIPT_DIR && python3 regions_table.py
  Run all checks     : cd $SCRIPT_DIR && ./lab1_verify.sh

  Open a NEW terminal (or run: source ~/.bashrc) for the 'labenv' alias to work.
  Activate the venv before ANY lab script that imports boto3 — this same venv
  is used by every later lab in the unit.

EOF
