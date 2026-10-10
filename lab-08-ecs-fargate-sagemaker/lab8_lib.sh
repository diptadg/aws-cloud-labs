#!/usr/bin/env bash
# CITS5503 Lab 8 - shared lab.env access for the shell scripts.
#
# Source this, then call lab_env_load. Values are exported as shell variables of
# the same name. lab_env_require names the first missing key and exits, so a
# script never runs half-configured.
#
# Override the file location with LAB_ENV=/some/other/path.

LAB_ENV_FILE="${LAB_ENV:-${HOME}/cits5503/lab.env}"

lab_env_load() {
  if [[ ! -f "${LAB_ENV_FILE}" ]]; then
    echo "${LAB_ENV_FILE} not found." >&2
    echo "Lab 1 creates this file. Run the Lab 1 setup before Lab 8." >&2
    exit 1
  fi

  local raw line key val first last
  while IFS= read -r raw || [[ -n "${raw}" ]]; do
    line="${raw%$'\r'}"                       # tolerate CRLF from Windows edits
    line="${line#"${line%%[![:space:]]*}"}"   # ltrim
    line="${line%"${line##*[![:space:]]}"}"   # rtrim

    [[ -z "${line}" || "${line}" == \#* ]] && continue
    [[ "${line}" == export\ * ]] && line="${line#export }"
    [[ "${line}" != *=* ]] && continue

    key="${line%%=*}"
    val="${line#*=}"
    key="${key//[[:space:]]/}"
    [[ "${key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue

    val="${val#"${val%%[![:space:]]*}"}"      # ltrim before testing for quotes
    val="${val%"${val##*[![:space:]]}"}"      # rtrim

    if (( ${#val} >= 2 )); then
      first="${val:0:1}"; last="${val: -1}"
      if [[ ( "${first}" == '"' && "${last}" == '"' ) \
         || ( "${first}" == "'" && "${last}" == "'" ) ]]; then
        val="${val:1:${#val}-2}"
      fi
    fi

    printf -v "${key}" '%s' "${val}"
    export "${key?}"
  done < "${LAB_ENV_FILE}"
}

lab_env_require() {
  local key missing=0
  for key in "$@"; do
    if [[ -z "${!key:-}" ]]; then
      echo "${key} is missing or empty in ${LAB_ENV_FILE}." >&2
      missing=1
    fi
  done
  if (( missing )); then
    echo "Add it there, or re-run ./lab8_prepare.sh to fill in the Lab 8 keys." >&2
    exit 1
  fi
}

# lab_env_optional KEY DEFAULT - a value with a documented, non-identity default
# (a base image, a port). Mirrors optional() in lab8_env.py. Unlike
# lab_env_require this never exits, so a lab.env written by an earlier version of
# the suite still works: the key simply takes its default.
lab_env_optional() {
  local key="$1" default="$2"
  if [[ -z "${!key:-}" ]]; then
    printf -v "${key}" '%s' "${default}"
    export "${key?}"
  fi
}

# lab_env_set KEY VALUE ... - insert or replace keys in lab.env, in place.
lab_env_set() {
  local key val tmp
  tmp="$(mktemp)"
  cp "${LAB_ENV_FILE}" "${tmp}"

  while (( $# >= 2 )); do
    key="$1"; val="$2"; shift 2
    if grep -qE "^[[:space:]]*(export[[:space:]]+)?${key}=" "${tmp}"; then
      # Escape the replacement so slashes and ampersands in values survive sed.
      local esc="${val//\\/\\\\}"; esc="${esc//&/\\&}"; esc="${esc//|/\\|}"
      sed -i -E "s|^([[:space:]]*(export[[:space:]]+)?)${key}=.*|\1${key}=\"${esc}\"|" "${tmp}"
    else
      printf '%s="%s"\n' "${key}" "${val}" >> "${tmp}"
    fi
    printf -v "${key}" '%s' "${val}"
    export "${key?}"
  done

  cat "${tmp}" > "${LAB_ENV_FILE}"
  rm -f "${tmp}"
  chmod 600 "${LAB_ENV_FILE}" 2>/dev/null || true
}

# lab_env_report KEY ... - print resolved values so a wrong one is obvious.
lab_env_report() {
  local key width=0
  for key in "$@"; do (( ${#key} > width )) && width=${#key}; done
  echo "Configuration from ${LAB_ENV_FILE}"
  for key in "$@"; do
    printf '  %-*s  %s\n' "${width}" "${key}" "${!key:-<unset>}"
  done
  echo
}
