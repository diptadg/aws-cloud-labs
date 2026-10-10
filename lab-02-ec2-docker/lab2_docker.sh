#!/usr/bin/env bash
#
# CITS5503 Lab 2 - Build and run an httpd container
#
# Scaffolds the html/ directory and Dockerfile from the lab sheet, builds the
# my-apache2 image, runs it as my-app, and verifies "Hello World!" is served.
#
# Tuned for Windows 11 + WSL2 (Ubuntu) with Docker Desktop WSL integration:
#   - does NOT apt-install docker.io; Docker Desktop already provides the CLI
#   - does NOT run `systemctl start docker`; the engine lives on the Windows side
#   - falls back to port 8080 if Windows has already reserved port 80
#
# Usage:
#   ./lab2_docker.sh up          # scaffold + build + run + test   (default)
#   ./lab2_docker.sh build       # scaffold + build only
#   ./lab2_docker.sh status      # docker ps -a, image list, curl test
#   ./lab2_docker.sh logs        # container logs
#   ./lab2_docker.sh down        # docker stop my-app && docker rm my-app
#   ./lab2_docker.sh clean       # down, plus remove the my-apache2 image
#   ./lab2_docker.sh up --port 8080
#
set -euo pipefail

LAB_DIR="${LAB_DIR:-$HOME/cits5503}"
BUILD_DIR="$LAB_DIR/lab2/docker"
# Image name, container name and port all come from lab.env.
LOADER="$(dirname "$0")/lab_env.sh"
[[ -f "$LOADER" ]] || LOADER="$LAB_DIR/lab2/lab_env.sh"
if [[ -f "$LOADER" ]]; then
  # shellcheck disable=SC1090
  source "$LOADER"
  load_lab_env >/dev/null 2>&1 || true
fi
IMAGE="${DOCKER_IMAGE:-my-apache2}"
CONTAINER="${DOCKER_CONTAINER:-my-app}"
PORT="${DOCKER_PORT:-80}"
PORT_OVERRIDDEN=0

ACTION="up"
while [[ $# -gt 0 ]]; do
  case "$1" in
    up|build|status|logs|down|clean) ACTION="$1"; shift ;;
    --port) PORT="${2:-80}"; PORT_OVERRIDDEN=1; shift 2 ;;
    -h|--help) awk 'NR>1 && /^#/{sub(/^# ?/,""); print; next} NR>1{exit}' "$0"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

c_reset=$'\033[0m'; c_blue=$'\033[1;34m'; c_green=$'\033[1;32m'
c_yellow=$'\033[1;33m'; c_red=$'\033[1;31m'; c_dim=$'\033[2m'
log()  { printf '\n%s==> %s%s\n' "$c_blue" "$1" "$c_reset"; }
ok()   { printf '    %s+%s %s\n' "$c_green" "$c_reset" "$1"; }
warn() { printf '    %s!%s %s\n' "$c_yellow" "$c_reset" "$1"; }
info() { printf '    %s%s%s\n' "$c_dim" "$1" "$c_reset"; }
die()  { printf '\n%sERROR:%s %s\n\n' "$c_red" "$c_reset" "$1" >&2; exit 1; }
show() { printf '    %s$ %s%s\n' "$c_dim" "$1" "$c_reset"; }

# ------------------------------------------------------------- docker check --
check_docker() {
  log "Checking Docker"

  if ! command -v docker >/dev/null; then
    die "docker command not found inside WSL.
     Open Docker Desktop on Windows, then:
       Settings > Resources > WSL integration > enable your Ubuntu distro
     and restart the shell. Do NOT 'apt install docker.io' - it will fight
     with Docker Desktop over the socket."
  fi

  if ! docker info >/dev/null 2>&1; then
    die "The Docker CLI is installed but cannot reach the engine.
     Start Docker Desktop on Windows and wait for the whale icon to settle,
     then re-run this script.
     (On Docker Desktop, 'sudo systemctl start docker' is not the fix - the
      engine runs in the docker-desktop WSL distro, not in Ubuntu.)"
  fi

  show "docker --version"
  docker --version | sed 's/^/    /'
  info "context: $(docker context show 2>/dev/null || echo unknown)"
  ok "Docker engine reachable"
}

# ----------------------------------------------------------- port conflicts --
check_port() {
  # Windows reserves ranges around port 80 for http.sys / IIS / WWW Publishing.
  # Docker's bind will fail with "bind: An attempt was made to access a socket..."
  if command -v ss >/dev/null && ss -ltn "sport = :$PORT" 2>/dev/null | grep -q LISTEN; then
    warn "Something is already listening on port $PORT inside WSL."
  fi
  if command -v curl >/dev/null && curl -fsS --max-time 2 "http://localhost:$PORT" >/dev/null 2>&1; then
    warn "http://localhost:$PORT already answers - another service may own it."
    warn "If the run fails, retry with:  ./lab2_docker.sh up --port 8080"
  fi
}

# ---------------------------------------------------------------- scaffold ---
scaffold() {
  log "Creating the build context"

  mkdir -p "$BUILD_DIR/html"

  # index.html goes INSIDE html/
  cat > "$BUILD_DIR/html/index.html" <<'HTML'
<html>
  <head> </head>
  <body>
    <p>Hello World!</p>
  </body>
</html>
HTML
  ok "$BUILD_DIR/html/index.html"

  # Dockerfile goes OUTSIDE html/, next to it
  cat > "$BUILD_DIR/Dockerfile" <<'DOCKERFILE'
FROM httpd:2.4
COPY ./html/ /usr/local/apache2/htdocs/
DOCKERFILE
  ok "$BUILD_DIR/Dockerfile"

  info "layout:"
  (cd "$BUILD_DIR" && find . -maxdepth 2 -not -path '*/.*' | sed 's/^/      /')
}

# ------------------------------------------------------------------- build ---
build_image() {
  log "Building the image"
  show "docker build -t $IMAGE ."
  (cd "$BUILD_DIR" && docker build -t "$IMAGE" .)
  ok "Image $IMAGE built"
  docker image ls "$IMAGE" | sed 's/^/    /'
}

# --------------------------------------------------------------------- run ---
run_container() {
  log "Running the container"

  if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    warn "Container $CONTAINER already exists - replacing it"
    docker rm -f "$CONTAINER" >/dev/null
  fi

  check_port

  show "docker run -p $PORT:80 -dit --name $CONTAINER $IMAGE"
  if ! docker run -p "$PORT:80" -dit --name "$CONTAINER" "$IMAGE" >/dev/null; then
    die "docker run failed. If the error mentions binding port $PORT, Windows
     has reserved it. Retry on another port:
       ./lab2_docker.sh up --port 8080"
  fi
  ok "Container $CONTAINER started"
}

# -------------------------------------------------------------------- test ---
test_page() {
  log "Verifying the page"

  local url="http://localhost:$PORT"
  for _ in $(seq 1 10); do
    if body=$(curl -fsS --max-time 3 "$url" 2>/dev/null); then
      echo "$body" | sed 's/^/    /'
      if grep -q "Hello World!" <<<"$body"; then
        ok "$url serves \"Hello World!\""
        return 0
      fi
      warn "$url answered but without \"Hello World!\" - is index.html inside html/?"
      return 1
    fi
    sleep 1
  done
  warn "Could not reach $url from inside WSL."
  warn "Try it in the Windows browser anyway - WSL localhost forwarding may still work."
  return 1
}

# ------------------------------------------------------------------ status ---
show_status() {
  log "Docker state"
  show "docker ps -a"
  docker ps -a | sed 's/^/    /'
  echo
  show "docker image ls $IMAGE"
  docker image ls "$IMAGE" | sed 's/^/    /'
  echo
  if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    curl -fsS --max-time 3 "http://localhost:$PORT" 2>/dev/null | sed 's/^/    /' \
      || warn "container is running but http://localhost:$PORT did not answer"
  else
    info "$CONTAINER is not running"
  fi
}

# ---------------------------------------------------------------- teardown ---
teardown() {
  log "Stopping and removing the container"
  if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    show "docker stop $CONTAINER"; docker stop "$CONTAINER" >/dev/null; ok "stopped"
  else
    info "$CONTAINER is not running"
  fi
  if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    show "docker rm $CONTAINER"; docker rm "$CONTAINER" >/dev/null; ok "removed"
  else
    info "$CONTAINER does not exist"
  fi
  show "docker ps -a"
  docker ps -a | sed 's/^/    /'
}

# --------------------------------------------------------------------- main --
case "$ACTION" in
  up)
    check_docker; scaffold; build_image; run_container; test_page || true
    log "Done"
    cat <<EOF

  Open in your Windows browser:
      http://localhost:$PORT     (or http://127.0.0.1:$PORT)

  Useful commands from the lab sheet:
      docker ps -a               list all containers, running and stopped
      docker stop $CONTAINER          stop it
      docker rm $CONTAINER            remove it

  Or just:  ./lab2_docker.sh down

EOF
    ;;
  build)  check_docker; scaffold; build_image ;;
  status) check_docker; show_status ;;
  logs)   check_docker; docker logs "$CONTAINER" ;;
  down)   check_docker; teardown ;;
  clean)
    check_docker; teardown
    log "Removing the image"
    docker image rm "$IMAGE" >/dev/null 2>&1 && ok "removed $IMAGE" || info "$IMAGE not present"
    ;;
esac
