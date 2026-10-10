#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# lab_fixperms.sh - repair ownership, permissions and line endings for
#                   CITS5503 lab scripts pulled in from Windows / re-downloaded.
#
# Run this every time you drop new scripts into ~/cits5503 (or after you have
# accidentally run a setup script with sudo and everything became root-owned).
#
# Usage:
#   ./lab_fixperms.sh                 # fixes ~/cits5503 and every lab*/ inside it
#   ./lab_fixperms.sh ~/cits5503 ~/labX
#   ./lab_fixperms.sh --dry-run       # show what would change, change nothing
#
# What it does:
#   1. Deletes Windows *:Zone.Identifier turds.
#   2. Strips CRLF -> LF from text files (fixes "bad interpreter: /bin/bash^M").
#   3. chowns everything back to you (uses sudo only if something is not yours).
#   4. Directories 755, plain files 644, lab.env 600.
#   5. Makes EVERY script executable, recursively, in the root dir and in every
#      lab1/ lab2/ lab3/ ... subfolder: anything ending .sh or .py, plus any
#      extensionless file that starts with a #! shebang.
#   6. Locks down ~/.ssh/*.pem to 400 so ssh stops refusing your key.
#   7. Prints a per-lab-folder summary of what is now runnable.
#
# Deliberately SKIPS .venv, .git, __pycache__, node_modules for the chmod pass
# (blanket-chmodding a venv would strip the +x off its binaries and break it),
# but still fixes ownership inside them.
# ---------------------------------------------------------------------------
set -euo pipefail

DRY_RUN=0
TARGETS=()

for arg in "$@"; do
  case "$arg" in
    -n|--dry-run) DRY_RUN=1 ;;
    -h|--help)
      sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    -*) echo "Unknown option: $arg" >&2; exit 1 ;;
    *)  TARGETS+=("$arg") ;;
  esac
done

# ---- who should own the files -------------------------------------------------
if [[ -n "${SUDO_USER:-}" ]]; then
  OWNER="$SUDO_USER"
else
  OWNER="$(id -un)"
fi
OWNER_GROUP="$(id -gn "$OWNER")"
OWNER_HOME="$(getent passwd "$OWNER" | cut -d: -f6)"

if [[ ${#TARGETS[@]} -eq 0 ]]; then
  TARGETS=("$OWNER_HOME/cits5503")
fi

run() {
  if [[ $DRY_RUN -eq 1 ]]; then
    printf '  [dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[ok]\033[0m %s\n' "$*"; }

# Prune expression reused by find: skip venvs/git/caches.
PRUNE=( -name .venv -o -name venv -o -name .git -o -name __pycache__ -o -name node_modules )

# True if the file begins with a #! shebang.
has_shebang() {
  [[ "$(head -c 2 -- "$1" 2>/dev/null)" == '#!' ]]
}

info "Target owner: ${OWNER}:${OWNER_GROUP}"
[[ $DRY_RUN -eq 1 ]] && warn "DRY RUN - nothing will actually be changed"

for DIR in "${TARGETS[@]}"; do
  if [[ ! -d "$DIR" ]]; then
    warn "Skipping '$DIR' - not a directory"
    continue
  fi
  DIR="$(cd "$DIR" && pwd -P)"
  info "Processing $DIR"

  # ---- 0. Windows drive check ------------------------------------------------
  FSTYPE="$(stat -f -c %T "$DIR" 2>/dev/null || echo unknown)"
  case "$DIR:$FSTYPE" in
    /mnt/[a-z]/*|*:9p|*:v9fs|*:drvfs|*:fuseblk)
      warn "'$DIR' looks like it lives on the Windows filesystem (fstype: $FSTYPE)."
      warn "chmod/chown are largely no-ops there and ssh will reject .pem files."
      warn "Strongly recommend moving your lab work to ${OWNER_HOME}/cits5503 instead."
      ;;
  esac

  # ---- 1. Zone.Identifier files ----------------------------------------------
  mapfile -t ZONE < <(find "$DIR" -type f -name '*:Zone.Identifier' 2>/dev/null || true)
  if [[ ${#ZONE[@]} -gt 0 ]]; then
    info "Removing ${#ZONE[@]} Zone.Identifier file(s)"
    for f in "${ZONE[@]}"; do run rm -f -- "$f"; done
  fi

  # ---- 2. CRLF -> LF ----------------------------------------------------------
  CRLF_FIXED=0
  while IFS= read -r -d '' f; do
    if grep -qU $'\r' "$f" 2>/dev/null; then
      run sed -i 's/\r$//' "$f"
      printf '    dos2unix: %s\n' "${f#"$DIR"/}"
      CRLF_FIXED=$((CRLF_FIXED + 1))
    fi
  done < <(find "$DIR" \( "${PRUNE[@]}" \) -prune -o \
             -type f \( -name '*.sh'   -o -name '*.py'  -o -name '*.env' \
                     -o -name '*.md'   -o -name '*.txt' -o -name '*.json' \
                     -o -name '*.yml'  -o -name '*.yaml' -o -name '*.cfg' \
                     -o -name '*.html' -o -name '*.pem' -o -name 'Dockerfile*' \) \
             -print0)
  [[ $CRLF_FIXED -gt 0 ]] && info "Converted $CRLF_FIXED file(s) from CRLF to LF"

  # ---- 3. Ownership -----------------------------------------------------------
  if find "$DIR" \( ! -user "$OWNER" -o ! -group "$OWNER_GROUP" \) -print -quit \
       2>/dev/null | grep -q .; then
    info "Found files not owned by $OWNER - fixing (sudo may prompt)"
    if [[ $DRY_RUN -eq 1 ]]; then
      printf '  [dry-run] sudo chown -R %s:%s %s\n' "$OWNER" "$OWNER_GROUP" "$DIR"
    else
      sudo chown -R "$OWNER:$OWNER_GROUP" "$DIR"
    fi
  else
    ok "Ownership already correct"
  fi

  # ---- 4. Baseline permissions ------------------------------------------------
  info "Normalising permissions"
  while IFS= read -r -d '' d; do run chmod 755 "$d"; done \
    < <(find "$DIR" \( "${PRUNE[@]}" \) -prune -o -type d -print0)
  while IFS= read -r -d '' f; do run chmod 644 "$f"; done \
    < <(find "$DIR" \( "${PRUNE[@]}" \) -prune -o -type f -print0)

  # ---- 5. Make every script executable (root dir + all lab*/ subfolders) ------
  # This walk is recursive, so lab1/, lab2/, lab3/... and anything nested inside
  # them are all covered. Two rules: known script extensions, or a #! shebang.
  EXEC_COUNT=0
  declare -A EXEC_BY_DIR=()
  while IFS= read -r -d '' f; do
    case "$f" in
      *.sh|*.py) : ;;
      *) has_shebang "$f" || continue ;;
    esac
    run chmod 755 "$f"
    EXEC_COUNT=$((EXEC_COUNT + 1))
    rel="${f#"$DIR"/}"
    sub="${rel%%/*}"
    [[ "$sub" == "$rel" ]] && sub="."          # file sits in the top-level dir
    EXEC_BY_DIR["$sub"]=$(( ${EXEC_BY_DIR["$sub"]:-0} + 1 ))
  done < <(find "$DIR" \( "${PRUNE[@]}" \) -prune -o -type f -print0)

  if [[ $EXEC_COUNT -gt 0 ]]; then
    info "Made $EXEC_COUNT script(s) executable:"
    while IFS= read -r sub; do
      [[ -z "$sub" ]] && continue
      label="$sub"
      [[ "$sub" == "." ]] && label="./ (top level)"
      printf '    %-18s %s script(s)\n' "$label" "${EXEC_BY_DIR[$sub]:-0}"
    done < <(printf '%s\n' "${!EXEC_BY_DIR[@]}" | sort -V)
  else
    warn "No scripts found under $DIR - is this the right directory?"
  fi

  # ---- 6. Secrets and keys ----------------------------------------------------
  while IFS= read -r -d '' f; do run chmod 600 "$f"; done \
    < <(find "$DIR" \( "${PRUNE[@]}" \) -prune -o \
          -type f \( -name 'lab.env' -o -name '.env' -o -name 'credentials' \) -print0)
  while IFS= read -r -d '' f; do run chmod 400 "$f"; done \
    < <(find "$DIR" \( "${PRUNE[@]}" \) -prune -o -type f -name '*.pem' -print0)

  # ---- 7. Flag lab folders that came out empty --------------------------------
  shopt -s nullglob
  for lab in "$DIR"/lab[0-9]*/; do
    labname="$(basename "$lab")"
    if [[ -z "${EXEC_BY_DIR[$labname]:-}" ]]; then
      warn "$labname/ contains no runnable scripts"
    fi
  done
  shopt -u nullglob

  unset EXEC_BY_DIR
  ok "Finished $DIR"
  echo
done

# ---- 8. SSH keys ---------------------------------------------------------------
SSH_DIR="$OWNER_HOME/.ssh"
if [[ -d "$SSH_DIR" ]] && compgen -G "$SSH_DIR/*.pem" > /dev/null; then
  info "Locking down $SSH_DIR/*.pem"
  run chmod 700 "$SSH_DIR"
  for k in "$SSH_DIR"/*.pem; do
    run chown "$OWNER:$OWNER_GROUP" "$k"
    run chmod 400 "$k"
    printf '    400 %s\n' "$k"
  done
fi

echo
ok "Done."
echo "Tip: add this to ~/.bashrc so you can just type 'fixccperms' after each download:"
echo "  alias fixccperms='$OWNER_HOME/cits5503/lab_fixperms.sh'"
