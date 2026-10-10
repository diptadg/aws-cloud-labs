#!/usr/bin/env bash
# CITS5503 Lab 6 - shared shell helpers
# Sourced by lab6_deploy.sh and lab6_verify.sh. Not meant to be run directly.

LAB_ENV="${LAB_ENV:-${HOME}/cits5503/lab.env}"

# ------------------------------------------------------------------ output
ok()   { printf '  \033[32m[ ok ]\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m[fail]\033[0m %s\n' "$*"; }
note() { printf '  \033[33m[note]\033[0m %s\n' "$*"; }
hdr()  { printf '\n\033[1m%s\033[0m\n' "$*"; }

# ------------------------------------------------------------------ lab.env
# Reads KEY=VALUE / export KEY="VALUE" lines without executing the file.
lab_env_load() {
  if [[ ! -f "${LAB_ENV}" ]]; then
    echo "ERROR: ${LAB_ENV} not found. Lab 1 creates this file." >&2
    exit 1
  fi

  local line key val
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"          # ltrim
    [[ -z "${line}" || "${line}" == \#* ]] && continue
    [[ "${line}" == export\ * ]] && line="${line#export }"
    [[ "${line}" != *=* ]] && continue

    key="${line%%=*}"
    val="${line#*=}"
    key="${key//[[:space:]]/}"
    val="${val#"${val%%[![:space:]]*}"}"             # ltrim
    val="${val%"${val##*[![:space:]]}"}"             # rtrim
    [[ "${val}" == \"*\" ]] && val="${val:1:${#val}-2}"
    [[ "${val}" == \'*\' ]] && val="${val:1:${#val}-2}"
    [[ "${key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue

    printf -v "${key}" '%s' "${val}"
    export "${key?}"
  done < "${LAB_ENV}"
}

# lab_env_require KEY [KEY ...] - fail loudly naming the first empty key.
lab_env_require() {
  local key
  for key in "$@"; do
    if [[ -z "${!key:-}" ]]; then
      echo "ERROR: ${key} is missing or empty in ${LAB_ENV}." >&2
      case "${key}" in
        LAB6_*) echo "       Run lab6_ec2.py (and lab6_alb.py) first." >&2 ;;
        AWS_DDB_TABLE) echo "       Run lab6_ddb_sync.py first." >&2 ;;
        *)      echo "       Add it to ${LAB_ENV} rather than hard-coding it." >&2 ;;
      esac
      exit 1
    fi
  done
}

# lab_env_set KEY VALUE - idempotent insert/replace in lab.env.
lab_env_set() {
  local key="$1" val="$2"
  if grep -qE "^(export[[:space:]]+)?${key}=" "${LAB_ENV}"; then
    sed -i -E "s|^(export[[:space:]]+)?${key}=.*|${key}=\"${val}\"|" "${LAB_ENV}"
  else
    printf '%s="%s"\n' "${key}" "${val}" >> "${LAB_ENV}"
  fi
  printf -v "${key}" '%s' "${val}"
  export "${key?}"
}

# ------------------------------------------------------------------ AWS
# Current public IP of an instance, looked up by Name tag (it changes on stop/start).
lab_instance_ip() {
  local name="$1"
  aws ec2 describe-instances \
    --region "${AWS_REGION}" \
    --filters "Name=tag:Name,Values=${name}" \
              "Name=instance-state-name,Values=running" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' \
    --output text 2>/dev/null
}

SSH_OPTS=(-o StrictHostKeyChecking=no
          -o UserKnownHostsFile=/dev/null
          -o LogLevel=ERROR
          -o ConnectTimeout=10)
