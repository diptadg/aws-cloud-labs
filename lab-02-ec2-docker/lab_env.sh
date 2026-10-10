#!/usr/bin/env bash
#
# CITS5503 - shared lab.env loader
#
# Sourced by the Lab 2 scripts. Parses ~/cits5503/lab.env and exposes every
# setting they need, so nothing is hardcoded in the scripts themselves.
#
#   source lab_env.sh
#   load_lab_env              # populates PREFIX, REGION, AMI, SG_NAME, ...
#   lab_env_report            # prints what was read and what was defaulted
#   lab_env_set VM1_ID i-abc  # merges a value back into lab.env
#
# Value formats accepted (they can be mixed in one file):
#   KEY=value            KEY="value"           KEY='value'
#   export KEY="value"   KEY = value           KEY="value"  # comment
# CRLF line endings and commented-out lines are handled.
#
# Values written back out are always double-quoted, matching the file's
# existing convention.
#
# shellcheck shell=bash

LAB_DIR="${LAB_DIR:-$HOME/cits5503}"
# LAB_ENV (the path override every lab honours) is read before the array of
# the same name below is declared, so the two do not collide.
LAB_ENV_FILE="${LAB_ENV_FILE:-${LAB_ENV:-$LAB_DIR/lab.env}}"

declare -gA LAB_ENV=()        # every key/value found in the file
declare -gA LAB_ENV_SOURCE=() # per setting: "lab.env", "--prefix" or "default"

# --------------------------------------------------------------- parsing ----
lab_env_parse() {
  local file="${1:-$LAB_ENV_FILE}" line key value quote
  [[ -f "$file" ]] || return 1

  LAB_ENV=()
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"                              # drop CR from CRLF files
    line="${line#"${line%%[![:space:]]*}"}"           # ltrim
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" == export\ * ]] && line="${line#export }"
    [[ "$line" == *=* ]] || continue

    key="${line%%=*}"
    value="${line#*=}"
    key="$(printf '%s' "$key" | tr -d '[:space:]')"
    [[ -n "$key" ]] || continue

    value="${value#"${value%%[![:space:]]*}"}"        # ltrim
    value="${value%"${value##*[![:space:]]}"}"        # rtrim

    if [[ "${value:0:1}" == '"' || "${value:0:1}" == "'" ]]; then
      quote="${value:0:1}"
      value="${value:1}"
      value="${value%%${quote}*}"                     # up to the closing quote
    else
      value="${value%%#*}"                            # unquoted: # is a comment
      value="${value%"${value##*[![:space:]]}"}"
    fi

    LAB_ENV["$key"]="$value"
  done < "$file"
  return 0
}

# lab_env_first KEY1 KEY2 ... -> first non-empty value, or empty
lab_env_first() {
  local k
  for k in "$@"; do
    if [[ -n "${LAB_ENV[$k]:-}" ]]; then
      printf '%s' "${LAB_ENV[$k]}"
      return 0
    fi
  done
  return 1
}

# ----------------------------------------------------------------- loading ---
# load_lab_env [override_prefix]
load_lab_env() {
  local override="${1:-}"

  if ! lab_env_parse "$LAB_ENV_FILE"; then
    printf '\n\033[1;31mERROR:\033[0m lab.env not found at %s\n' "$LAB_ENV_FILE" >&2
    printf '  Run the Lab 1 setup script, or point at it:\n' >&2
    printf '    LAB_ENV_FILE=/path/to/lab.env %s\n\n' "$(basename "${BASH_SOURCE[1]:-script}")" >&2
    return 1
  fi

  # --- resource-name prefix -------------------------------------------------
  if [[ -n "$override" ]]; then
    PREFIX="$override"; LAB_ENV_SOURCE[PREFIX]="--prefix"
  else
    PREFIX="$(lab_env_first LAB_PREFIX || true)"
    LAB_ENV_SOURCE[PREFIX]="lab.env"
  fi
  if [[ ! "$PREFIX" =~ ^[a-z0-9][a-z0-9-]{1,8}$ ]]; then
    printf '\n\033[1;31mERROR:\033[0m no valid LAB_PREFIX in %s (got %s)\n' \
      "$LAB_ENV_FILE" "'${PREFIX:-<none>}'" >&2
    printf '  Expected a line like:  LAB_PREFIX="myname"   (2-9 chars: a-z, 0-9, -)\n\n' >&2
    return 1
  fi

  # --- region and AMI -------------------------------------------------------
  LAB_ENV_WARNINGS=()
  if REGION="$(lab_env_first AWS_LAB_REGION AWS_REGION AWS_DEFAULT_REGION LAB_REGION REGION)"; then
    LAB_ENV_SOURCE[REGION]="lab.env"
  else
    REGION="ap-southeast-2"; LAB_ENV_SOURCE[REGION]="default"
  fi

  if AMI="$(lab_env_first AWS_LAB_AMI AWS_AMI AMI_ID LAB_AMI IMAGE_ID AMI)"; then
    LAB_ENV_SOURCE[AMI]="lab.env"
  else
    printf '\n\033[1;31mERROR:\033[0m no AMI id in %s - set AWS_AMI (see config.example.env).\n\n' \
      "$LAB_ENV_FILE" >&2
    return 1
  fi

  # --- instance type --------------------------------------------------------
  if INSTANCE_TYPE="$(lab_env_first INSTANCE_TYPE AWS_LAB_INSTANCE_TYPE)"; then
    LAB_ENV_SOURCE[INSTANCE_TYPE]="lab.env"
  else
    INSTANCE_TYPE="t3.micro"; LAB_ENV_SOURCE[INSTANCE_TYPE]="default"
  fi
  if [[ "$INSTANCE_TYPE" != "t3.micro" ]]; then
    LAB_ENV_WARNINGS+=("instance type is '$INSTANCE_TYPE'; the lab requires t3.micro")
  fi

  # --- resource names (awscli side) ----------------------------------------
  SG_NAME="$(lab_env_first SG_NAME SECURITY_GROUP_NAME  || printf '%s-sg'  "$PREFIX")"
  KEY_NAME="$(lab_env_first KEY_NAME KEY_PAIR_NAME      || printf '%s-key' "$PREFIX")"
  VM1_NAME="$(lab_env_first VM1_NAME INSTANCE1_NAME     || printf '%s-vm1' "$PREFIX")"
  KEY_FILE="$(lab_env_first KEY_FILE KEY_PATH           || printf '%s/.ssh/%s.pem' "$HOME" "$KEY_NAME")"

  # --- resource names (boto3 side, deliberately distinct) ------------------
  SG_NAME_BOTO="$(lab_env_first SG_NAME_BOTO SG_NAME_2   || printf '%s-sg-boto'  "$PREFIX")"
  KEY_NAME_BOTO="$(lab_env_first KEY_NAME_BOTO KEY_NAME_2 || printf '%s-key-boto' "$PREFIX")"
  VM2_NAME="$(lab_env_first VM2_NAME INSTANCE2_NAME      || printf '%s-vm2' "$PREFIX")"
  KEY_FILE_BOTO="$(lab_env_first KEY_FILE_BOTO           || printf '%s/.ssh/%s.pem' "$HOME" "$KEY_NAME_BOTO")"

  # --- docker ---------------------------------------------------------------
  DOCKER_IMAGE="$(lab_env_first DOCKER_IMAGE     || printf 'my-apache2')"
  DOCKER_CONTAINER="$(lab_env_first DOCKER_CONTAINER || printf 'my-app')"
  DOCKER_PORT="$(lab_env_first DOCKER_PORT       || printf '80')"

  export PREFIX REGION AMI INSTANCE_TYPE
  export SG_NAME KEY_NAME KEY_FILE VM1_NAME
  export SG_NAME_BOTO KEY_NAME_BOTO KEY_FILE_BOTO VM2_NAME
  export DOCKER_IMAGE DOCKER_CONTAINER DOCKER_PORT
  return 0
}

# ---------------------------------------------------------------- report -----
lab_env_report() {
  local dim=$'\033[2m' green=$'\033[1;32m' yellow=$'\033[1;33m' reset=$'\033[0m'
  printf '    %s+%s Loaded %s (%d keys)\n' "$green" "$reset" "$LAB_ENV_FILE" "${#LAB_ENV[@]}"
  local pairs=(
    "prefix:$PREFIX:${LAB_ENV_SOURCE[PREFIX]:-lab.env}"
    "region:$REGION:${LAB_ENV_SOURCE[REGION]:-default}"
    "AMI id:$AMI:${LAB_ENV_SOURCE[AMI]:-lab.env}"
    "instance type:$INSTANCE_TYPE:${LAB_ENV_SOURCE[INSTANCE_TYPE]:-default}"
  )
  local p label value src
  for p in "${pairs[@]}"; do
    IFS=: read -r label value src <<<"$p"
    printf '      %-16s %-22s %s(%s)%s\n' "$label" "$value" "$dim" "$src" "$reset"
  done
  local w
  for w in "${LAB_ENV_WARNINGS[@]:-}"; do
    [[ -n "$w" ]] && printf '    %s!%s %s\n' "$yellow" "$reset" "$w"
  done
  # Must return 0: callers run under `set -e`, and the loop above would
  # otherwise leave a non-zero status behind and abort the script.
  return 0
}

# ------------------------------------------------------------------ write ----
# lab_env_set KEY VALUE - update in place if present, append otherwise.
# Other lines, comments and ordering are preserved.
lab_env_set() {
  local key="$1" value="$2" tmp
  [[ -n "$key" ]] || return 1
  [[ -f "$LAB_ENV_FILE" ]] || return 1

  tmp="$(mktemp "${LAB_ENV_FILE}.XXXXXX")" || return 1
  KEY="$key" VALUE="$value" awk '
    BEGIN { key = ENVIRON["KEY"]; value = ENVIRON["VALUE"]; done = 0 }
    {
      line = $0
      probe = line
      sub(/\r$/, "", probe)
      sub(/^[ \t]*/, "", probe)
      sub(/^export[ \t]+/, "", probe)
      split(probe, parts, "=")
      name = parts[1]
      gsub(/[ \t]/, "", name)
      if (!done && probe !~ /^#/ && index(probe, "=") > 0 && name == key) {
        print key "=\"" value "\""
        done = 1
      } else {
        print line
      }
    }
    END { if (!done) print key "=\"" value "\"" }
  ' "$LAB_ENV_FILE" > "$tmp" && mv "$tmp" "$LAB_ENV_FILE" || { rm -f "$tmp"; return 1; }

  LAB_ENV["$key"]="$value"
  return 0
}
