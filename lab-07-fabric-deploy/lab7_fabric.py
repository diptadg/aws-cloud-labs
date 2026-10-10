#!/usr/bin/env python3
"""
CITS5503 Lab 7 - deploy Django + nginx with Fabric
---------------------------------------------------
This is the Fabric script. It converts the manual steps from Lab 6 ("Set up an
EC2 instance" and "Set up Django inside the created EC2 instance") into Fabric
commands, so one run takes a bare Ubuntu instance to a working
http://<instance-public-ip>/polls/ serving "Hello, world."

Checkpoint 1 asks you to open this file and show three things. Each has its own
banner below, in the order they run:

    1. Connecting to your EC2 instance
           -> connect()                        "CHECKPOINT 1 - part 1 of 3"
    2. Configuring nginx to proxy requests to the Django development server
           -> configure_nginx()                "CHECKPOINT 1 - part 2 of 3"
    3. Launching the Django development server on port 8000 in the background
           -> start_django_in_background()     "CHECKPOINT 1 - part 3 of 3"

Everything is idempotent: re-running rewrites the config files and restarts the
server rather than creating a second project or a second copy of anything.

Run with the shared venv active:
    labenv
    python3 lab7_fabric.py

    python3 lab7_fabric.py --runner nohup    # background with nohup, not systemd
    python3 lab7_fabric.py --restart-only    # just bounce the dev server
    python3 lab7_fabric.py --upgrade         # include apt-get upgrade (slow)
"""

import argparse
import re
import shlex
import sys
import time
import urllib.error
import urllib.request
from io import StringIO
from uuid import uuid4

try:
    from fabric import Connection
except ImportError:
    sys.exit("fabric not found. Run ./lab7_prepare.sh first.")

from lab7_env import load_env, require, optional, update_env, report

# Fixed by the lab specification, not configuration.
BASE = "/opt/wwc/mysites"          # the directory path the lab prescribes
VENV = f"{BASE}/myvenv"            # the virtualenv the lab prescribes
PROJECT = f"{BASE}/lab"            # django-admin startproject lab
APP = f"{PROJECT}/polls"           # manage.py startapp polls
NGINX_SITE = "/etc/nginx/sites-enabled/default"
SERVICE = "django-polls"
LOG_FILE = "/var/log/django-polls.log"
APT_PACKAGES = "python3-venv python3-pip nginx curl"

cfg = load_env()
VM_NAME, VM_IP = require(cfg, "LAB7_VM_NAME", "LAB7_VM_IP")
DJANGO_PORT = optional(cfg, "DJANGO_PORT", "8000")


# --------------------------------------------------------------- helpers
def banner(text: str) -> None:
    print(f"\n== {text}")


def sudo_sh(c, script: str, **kwargs):
    """Run a shell snippet as root.

    Fabric builds sudo as `sudo -S -p '<prompt>' <command>` by simple string
    concatenation, so `cd`, `&&`, pipes and redirects in a bare c.sudo() would
    be interpreted by the *login* user's shell, not by root. Wrapping the whole
    snippet in `bash -c` keeps it inside the root shell where it belongs.
    """
    return c.sudo(f"bash -c {shlex.quote(script)}", **kwargs)


def sudo_write(c, content: str, dest: str, mode: str = "644") -> None:
    """Upload text and install it at a root-owned path.

    /opt/wwc and /etc are root-owned, and Fabric's SFTP session is the ubuntu
    user, so the file is staged in /tmp and moved into place with `install`.
    """
    tmp = f"/tmp/lab7-{uuid4().hex}"
    c.put(StringIO(content), tmp)
    c.sudo(f"install -o root -g root -m {mode} {tmp} {dest}", hide=True)
    c.run(f"rm -f {tmp}", hide=True)
    print(f"   wrote {dest}")


def apt(c, args: str, tries: int = 6):
    """apt-get, retried while cloud-init or unattended-upgrades holds the lock."""
    for attempt in range(1, tries + 1):
        r = sudo_sh(c, f"DEBIAN_FRONTEND=noninteractive apt-get {args}",
                    warn=True, hide=True, pty=False)
        if r.ok:
            return r
        print(f"   apt-get busy (attempt {attempt}/{tries}), waiting 15s")
        time.sleep(15)
    print(r.stderr.strip()[-800:] or r.stdout.strip()[-800:])
    sys.exit(f"apt-get {args} kept failing on {VM_NAME}.")


# ============================================================================
# CHECKPOINT 1 - part 1 of 3: connecting to your EC2 instance
# ============================================================================
def connect(host: str) -> Connection:
    """Open the Fabric connection to the EC2 instance.

    Host, user and identity file all come from the `Host <instance name>` block
    lab7_sshconfig.py wrote into ~/.ssh/config, which Fabric loads by default -
    so the connection needs nothing but the instance name, exactly as in the
    lab sheet's example.
    """
    print(f"Connecting to {host} ...")

    # Retry while sshd finishes booting on a freshly launched instance. The
    # Connection is rebuilt each time rather than reused, so a half-open client
    # left behind by a refused handshake cannot poison later attempts.
    last = None
    for attempt in range(1, 21):
        try:
            connection = Connection(host, connect_timeout=15)
            result = connection.run("uname -s", hide=True)
            print(f"   connected: uname -s -> {result.stdout.strip()}")
            print(f"   user      : {connection.user}@{connection.host}:{connection.port}")
            return connection
        except Exception as exc:  # noqa: BLE001 - surface whatever paramiko raised
            last = exc
            print(f"   attempt {attempt}/20 not ready ({type(exc).__name__}); waiting 10s")
            time.sleep(10)

    sys.exit(
        f"Could not connect to {host}.\nLast error: {last}\n\n"
        "Check, in order: outbound TCP 22 from this network (UWA wifi blocks it),\n"
        "port 22 inbound on the security group, the key file's mode and location,\n"
        "and whether ~/.ssh/config still has the instance's previous IP."
    )


# --------------------------------------------------- packages and the venv
def install_packages(c, do_upgrade: bool) -> None:
    """apt-get update / upgrade, then the Python venv package and nginx."""
    banner("Installing packages")

    # A just-booted instance still has cloud-init holding the dpkg lock.
    print("   waiting for cloud-init to finish ...")
    c.sudo("cloud-init status --wait", warn=True, hide=True, pty=False, timeout=600)

    apt(c, "update -y")
    print("   apt-get update done")

    if do_upgrade:
        print("   apt-get upgrade (this takes a few minutes) ...")
        apt(c, '-y -o Dpkg::Options::="--force-confdef" '
               '-o Dpkg::Options::="--force-confold" upgrade')
        print("   apt-get upgrade done")

    apt(c, f"install -y {APT_PACKAGES}")
    print(f"   installed: {APT_PACKAGES}")


def create_virtualenv(c) -> None:
    """/opt/wwc/mysites, then python3 -m venv myvenv, then pip install django.

    The lab activates the venv with `source myvenv/bin/activate`. Calling the
    venv's own pip and python3 by absolute path does the same thing and does
    not depend on a shell staying sourced between Fabric commands.
    """
    banner(f"Setting up the virtual environment in {BASE}")

    c.sudo(f"mkdir -p {BASE}", hide=True)

    if c.sudo(f"test -x {VENV}/bin/python3", warn=True, hide=True).ok:
        print(f"   {VENV} already exists")
    else:
        sudo_sh(c, f"cd {BASE} && python3 -m venv myvenv", pty=False)
        print(f"   created {VENV}")

    sudo_sh(c, f"{VENV}/bin/pip install --quiet --upgrade pip", hide=True, pty=False)
    sudo_sh(c, f"{VENV}/bin/pip install --quiet django", hide=True, pty=False)
    version = c.sudo(f"{VENV}/bin/python3 -m django --version", hide=True).stdout.strip()
    print(f"   django {version}")


def create_django_project(c) -> None:
    """django-admin startproject lab, then manage.py startapp polls."""
    banner("Creating the Django project and the polls app")

    if c.sudo(f"test -f {PROJECT}/manage.py", warn=True, hide=True).ok:
        print(f"   project {PROJECT} already exists")
    else:
        sudo_sh(c, f"cd {BASE} && {VENV}/bin/django-admin startproject lab", pty=False)
        print(f"   created {PROJECT}")

    if c.sudo(f"test -f {APP}/apps.py", warn=True, hide=True).ok:
        print(f"   app {APP} already exists")
    else:
        sudo_sh(c, f"cd {PROJECT} && {VENV}/bin/python3 manage.py startapp polls", pty=False)
        print(f"   created {APP}")


def write_application_files(c) -> None:
    """The three files from the lab sheet, plus the one settings.py change."""
    banner("Writing the polls views and URL configuration")

    sudo_write(c, (
        "from django.http import HttpResponse\n"
        "\n"
        "\n"
        "def index(request):\n"
        '    return HttpResponse("Hello, world.")\n'
    ), f"{APP}/views.py")

    sudo_write(c, (
        "from django.urls import path\n"
        "\n"
        "from . import views\n"
        "\n"
        "urlpatterns = [\n"
        "    path('', views.index, name='index'),\n"
        "]\n"
    ), f"{APP}/urls.py")

    sudo_write(c, (
        "from django.urls import include, path\n"
        "from django.contrib import admin\n"
        "\n"
        "urlpatterns = [\n"
        "    path('polls/', include('polls.urls')),\n"
        "    path('admin/', admin.site.urls),\n"
        "]\n"
    ), f"{PROJECT}/lab/urls.py")

    # nginx forwards with Host: 127.0.0.1:8000 by default, which Django accepts
    # while DEBUG is on. Widening ALLOWED_HOSTS keeps the page reachable if the
    # proxy is ever changed to pass the real Host through.
    settings_path = f"{PROJECT}/lab/settings.py"
    text = c.sudo(f"cat {settings_path}", hide=True).stdout
    if "ALLOWED_HOSTS = ['*']" not in text:
        patched, hits = re.subn(r"ALLOWED_HOSTS\s*=\s*\[[^\]]*\]",
                                "ALLOWED_HOSTS = ['*']", text, count=1)
        if hits:
            sudo_write(c, patched, settings_path)
            print("   ALLOWED_HOSTS set to ['*']")
        else:
            print("   WARNING: could not find ALLOWED_HOSTS in settings.py")
    else:
        print("   ALLOWED_HOSTS already ['*']")

    # Keeps "unapplied migrations" noise out of the runserver log.
    sudo_sh(c, f"cd {PROJECT} && {VENV}/bin/python3 manage.py migrate --noinput",
            warn=True, hide=True, pty=False)


# ============================================================================
# CHECKPOINT 1 - part 2 of 3: configuring nginx to proxy requests to the
#                             Django development server
# ============================================================================
def configure_nginx(c) -> None:
    """Replace /etc/nginx/sites-enabled/default so port 80 proxies to Django.

    nginx listens on 80 (what the browser and the marker hit) and forwards
    every request to the development server on 127.0.0.1:8000, which is bound
    to localhost only and is not reachable from outside the instance.
    """
    banner(f"Configuring nginx to proxy port 80 -> 127.0.0.1:{DJANGO_PORT}")

    site = (
        "server {\n"
        "  listen 80 default_server;\n"
        "  listen [::]:80 default_server;\n"
        "\n"
        "  location / {\n"
        "    proxy_set_header X-Forwarded-Host $host;\n"
        "    proxy_set_header X-Real-IP $remote_addr;\n"
        "\n"
        f"    proxy_pass http://127.0.0.1:{DJANGO_PORT};\n"
        "  }\n"
        "}\n"
    )
    sudo_write(c, site, NGINX_SITE)

    c.sudo("nginx -t", hide=True)
    print("   nginx -t passed")

    c.sudo("systemctl restart nginx", hide=True)
    c.sudo("systemctl enable nginx", warn=True, hide=True)
    print("   nginx restarted and enabled at boot")


# ============================================================================
# CHECKPOINT 1 - part 3 of 3: launching the Django development server on
#                             port 8000 in the background
# ============================================================================
def start_django_in_background(c, runner: str) -> None:
    """Start `manage.py runserver 8000` so it outlives this Fabric run.

    Two ways to detach, both leaving the same process listening on 8000:

      systemd  (default)  a django-polls unit; survives reboots, restarts if the
                          server crashes, and `journalctl -u django-polls -f`
                          shows requests arriving live.
      nohup               setsid + nohup + full stream redirection, so the SSH
                          channel closes instead of blocking on an open pipe.

    Whichever is chosen, the other is stopped first, so a re-run never leaves
    two servers fighting over port 8000.
    """
    banner(f"Starting the development server on port {DJANGO_PORT} in the background "
           f"({runner})")

    run_cmd = f"{VENV}/bin/python3 manage.py runserver {DJANGO_PORT}"

    # Clear whatever the previous run left behind.
    c.sudo(f"systemctl stop {SERVICE}", warn=True, hide=True)
    # The bracket is deliberate. `pkill -f 'manage.py runserver'` also matches
    # the sudo command line carrying that very pattern, so it kills the shell
    # running it. '[m]anage.py runserver' matches the server and not itself.
    c.sudo("pkill -f '[m]anage.py runserver'", warn=True, hide=True)
    time.sleep(1)

    if runner == "systemd":
        unit = (
            "[Unit]\n"
            "Description=CITS5503 Lab 7 Django development server\n"
            "After=network.target\n"
            "\n"
            "[Service]\n"
            "Type=simple\n"
            "User=root\n"
            f"WorkingDirectory={PROJECT}\n"
            "Environment=PYTHONUNBUFFERED=1\n"
            f"ExecStart={run_cmd}\n"
            "Restart=always\n"
            "RestartSec=3\n"
            "\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )
        sudo_write(c, unit, f"/etc/systemd/system/{SERVICE}.service")
        c.sudo("systemctl daemon-reload", hide=True)
        c.sudo(f"systemctl enable {SERVICE}", warn=True, hide=True)
        c.sudo(f"systemctl restart {SERVICE}", hide=True)
        print(f"   systemd unit {SERVICE}.service started and enabled at boot")
    else:
        c.sudo(f"systemctl disable {SERVICE}", warn=True, hide=True)
        c.sudo(f"touch {LOG_FILE}", hide=True)
        # The braces matter. Written as `cd ... && setsid ... &`, bash
        # backgrounds the whole `cd && setsid` list in a subshell whose stdout
        # and stderr are still the SSH channel, so Fabric blocks until the
        # development server exits - i.e. forever. Grouping the list and
        # redirecting the group leaves no stream attached to the channel, and
        # setsid puts the server in its own session so it survives the logout.
        sudo_sh(c, (
            f"{{ cd {PROJECT} && setsid nohup {run_cmd} ; }} "
            f">> {LOG_FILE} 2>&1 < /dev/null &"
        ), pty=False, hide=True)
        print(f"   detached with setsid+nohup, output appended to {LOG_FILE}")

    # Confirm something is actually listening before moving on.
    for _ in range(20):
        listening = c.run(
            f"ss -ltn 2>/dev/null | grep -q ':{DJANGO_PORT} ' && echo yes || echo no",
            hide=True, warn=True).stdout.strip()
        if listening == "yes":
            print(f"   listening on 127.0.0.1:{DJANGO_PORT}")
            return
        time.sleep(2)

    print(f"   WARNING: nothing is listening on {DJANGO_PORT} yet")
    show_logs(c, runner)


def show_logs(c, runner: str) -> None:
    print("\n   Last 20 lines of the server log:")
    if runner == "systemd":
        out = c.sudo(f"journalctl -u {SERVICE} -n 20 --no-pager",
                     warn=True, hide=True).stdout
    else:
        out = c.sudo(f"tail -n 20 {LOG_FILE}", warn=True, hide=True).stdout
    for line in out.splitlines():
        print(f"     {line}")


# ------------------------------------------------------------- smoke tests
def smoke_test(c, runner: str) -> bool:
    banner("Smoke test on the instance")

    code = c.run(
        f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{DJANGO_PORT}/polls/",
        warn=True, hide=True).stdout.strip()
    print(f"   django  http://127.0.0.1:{DJANGO_PORT}/polls/  -> {code or 'no response'}")

    code = c.run("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/polls/",
                 warn=True, hide=True).stdout.strip()
    print(f"   nginx   http://127.0.0.1/polls/  -> {code or 'no response'}")

    if code != "200":
        show_logs(c, runner)
        return False

    body = c.run("curl -s http://127.0.0.1/polls/", hide=True).stdout.strip()
    print(f"   body    {body!r}")
    return True


def check_from_here(url: str) -> bool:
    """The Checkpoint 2 request, made from this machine rather than the instance."""
    banner("Checking from this machine")
    print(f"   GET {url}")
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            body = resp.read().decode("utf-8", "replace").strip()
        print(f"   {resp.status}  {body!r}")
        if "Hello, world." in body:
            print("   page shows 'Hello, world.'")
            return True
        print("   WARNING: the page loaded but does not contain 'Hello, world.'")
        return False
    except urllib.error.HTTPError as e:
        print(f"   HTTP {e.code}")
    except Exception as exc:  # noqa: BLE001
        print(f"   {type(exc).__name__}: {exc}")
    print("   Outbound port 80 is commonly blocked on UWA wifi. If the instance-side")
    print("   test above returned 200, the deployment is fine and it is this network.")
    return False


# --------------------------------------------------------------------- run
def main() -> None:
    ap = argparse.ArgumentParser(description="Deploy Django + nginx with Fabric.")
    ap.add_argument("--host", default=VM_NAME,
                    help=f"ssh config Host alias to connect to (default: {VM_NAME})")
    ap.add_argument("--runner", choices=("systemd", "nohup"), default="systemd",
                    help="how the development server is backgrounded (default: systemd)")
    ap.add_argument("--upgrade", action="store_true",
                    help="also run apt-get upgrade (adds several minutes)")
    ap.add_argument("--restart-only", action="store_true",
                    help="skip the build steps and just restart the development server")
    args = ap.parse_args()

    url = f"http://{VM_IP}/polls/"
    report({
        "Host alias": args.host,
        "Public IP": VM_IP,
        "Django port": DJANGO_PORT,
        "Base directory": BASE,
        "Backgrounded by": args.runner,
        "Target URL": url,
    })

    c = connect(args.host)

    if not args.restart_only:
        install_packages(c, args.upgrade)
        create_virtualenv(c)
        create_django_project(c)
        write_application_files(c)
        configure_nginx(c)

    start_django_in_background(c, args.runner)
    local_ok = False
    if smoke_test(c, args.runner):
        local_ok = check_from_here(url)

    c.close()

    # Recorded so lab7_verify.sh checks the unit this run actually created,
    # instead of carrying its own copy of the name.
    update_env({"LAB7_SERVICE": SERVICE, "LAB7_RUNNER": args.runner})

    print()
    print("Deployment finished.")
    print(f"  Open in your browser:  {url}")
    print("  It should display:     Hello, world.")
    if not local_ok:
        print("\n  Then run ./lab7_verify.sh for a full rehearsal of the checkpoints.")


if __name__ == "__main__":
    main()
