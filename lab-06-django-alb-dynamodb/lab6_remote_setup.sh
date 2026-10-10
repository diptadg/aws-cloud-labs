#!/usr/bin/env bash
# CITS5503 Lab 6 - runs ON the EC2 instance (pushed there by lab6_deploy.sh)
#
# Does, in order, exactly what "Set up an EC2 instance" and "Set up Django
# inside the created EC2 instance" ask for:
#   python3-venv + nginx, /opt/wwc/mysites, a venv, django, the lab project,
#   the polls app, the nginx reverse proxy, and the development server on
#   port 8000 - here kept running by systemd so it survives your SSH session.
#
# Expects these in the environment (lab6_deploy.sh passes them):
#   AWS_REGION  AWS_DDB_TABLE  VM_NAME  DJANGO_PORT  CRED_STAGE

set -euo pipefail

# The lab says "change the bash to operate as sudo" - do it once, up front.
# The variables are passed explicitly rather than with `sudo -E`, which the
# default sudoers policy is not guaranteed to allow.
if [[ "${EUID}" -ne 0 ]]; then
  exec sudo \
    AWS_REGION="${AWS_REGION:-}" \
    AWS_DDB_TABLE="${AWS_DDB_TABLE:-}" \
    VM_NAME="${VM_NAME:-}" \
    DJANGO_PORT="${DJANGO_PORT:-}" \
    CRED_STAGE="${CRED_STAGE:-}" \
    bash "$0" "$@"
fi

AWS_REGION="${AWS_REGION:?AWS_REGION not passed through}"
AWS_DDB_TABLE="${AWS_DDB_TABLE:?AWS_DDB_TABLE not passed through}"
VM_NAME="${VM_NAME:-lab6-vm}"
DJANGO_PORT="${DJANGO_PORT:-8000}"
CRED_STAGE="${CRED_STAGE:-/home/ubuntu/.aws-stage}"

SITE_ROOT="/opt/wwc/mysites"
VENV="${SITE_ROOT}/myvenv"
PROJECT="${SITE_ROOT}/lab"
SERVICE="django-polls"

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# --------------------------------------------------------------- packages
step "Installing python3-venv and nginx"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y -qq
apt-get install -y -qq python3-venv python3-pip nginx

# ------------------------------------------------------------ credentials
# The Django view scans AWS DynamoDB, so the instance needs credentials.
# They go in /root/.aws (the service runs as root) and are picked up by the
# normal boto3 credential chain - no keys pasted into views.py.
step "Installing AWS credentials for the app"
if [[ -f "${CRED_STAGE}/credentials" ]]; then
  install -d -m 700 /root/.aws
  install -m 600 "${CRED_STAGE}/credentials" /root/.aws/credentials
  printf '[default]\nregion = %s\noutput = json\n' "${AWS_REGION}" > /root/.aws/config
  chmod 600 /root/.aws/config
  rm -rf "${CRED_STAGE}"
  echo "    credentials installed at /root/.aws/credentials"
else
  echo "    no staged credentials found - the /polls/ page will show a DynamoDB error"
fi

# ------------------------------------------------------- venv and project
step "Setting up the Python 3 virtual environment"
mkdir -p "${SITE_ROOT}"
[[ -d "${VENV}" ]] || python3 -m venv "${VENV}"
"${VENV}/bin/pip" install --quiet --upgrade pip
"${VENV}/bin/pip" install --quiet django boto3
echo "    $("${VENV}/bin/python3" -m django --version 2>/dev/null | sed 's/^/Django /')"

step "Creating the Django project and the polls app"
cd "${SITE_ROOT}"
[[ -d "${PROJECT}" ]] || "${VENV}/bin/django-admin" startproject lab
cd "${PROJECT}"
[[ -d "${PROJECT}/polls" ]] || "${VENV}/bin/python3" manage.py startapp polls

# ------------------------------------------------------------- app files
step "Writing polls/views.py, polls/urls.py, lab/urls.py and the template"

mkdir -p "${PROJECT}/polls/templates"

# views.py - scans the AWS DynamoDB table and renders the file list.
cat > "${PROJECT}/polls/views.py" <<EOF
from django.http import HttpResponse
from django.template import loader

import boto3
from botocore.exceptions import BotoCoreError, ClientError

AWS_REGION = "${AWS_REGION}"
TABLE_NAME = "${AWS_DDB_TABLE}"
INSTANCE_NAME = "${VM_NAME}"


def index(request):
    """Render the CloudStorage file list from the AWS DynamoDB table."""
    template = loader.get_template("files.html")

    items = []
    error = None
    try:
        dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
        table = dynamodb.Table(TABLE_NAME)

        response = table.scan()
        items = response.get("Items", [])
        while "LastEvaluatedKey" in response:
            response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
            items.extend(response.get("Items", []))
    except (ClientError, BotoCoreError) as exc:
        error = str(exc)

    items.sort(key=lambda item: item.get("path", item.get("fileName", "")))

    context = {
        "items": items,
        "error": error,
        "table": TABLE_NAME,
        "region": AWS_REGION,
        "instance": INSTANCE_NAME,
    }
    return HttpResponse(template.render(context, request))
EOF

cat > "${PROJECT}/polls/urls.py" <<'EOF'
from django.urls import path

from . import views

urlpatterns = [
    path('', views.index, name='index'),
]
EOF

cat > "${PROJECT}/lab/urls.py" <<'EOF'
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('polls/', include('polls.urls')),
    path('admin/', admin.site.urls),
]
EOF

cat > "${PROJECT}/polls/templates/files.html" <<'EOF'
<html>
<head>
    <title>Files</title>
</head>
<body>
    <p>Hello, world.</p>

    <h1>Files</h1>

    {% if error %}
    <p>Could not read DynamoDB table {{ table }} in {{ region }}: {{ error }}</p>
    {% endif %}

    <ul>
        {% for item in items %}
          <li>{{ item.fileName }} &mdash; {{ item.path }} &mdash; {{ item.lastUpdated }}</li>
        {% empty %}
          <li>No records in {{ table }}.</li>
        {% endfor %}
    </ul>

    <p>Served by {{ instance }} from DynamoDB table {{ table }} ({{ region }}).</p>
</body>
</html>
EOF

step "Patching lab/settings.py"
"${VENV}/bin/python3" - "${PROJECT}/lab/settings.py" <<'PY'
import re
import sys
from pathlib import Path

settings = Path(sys.argv[1])
text = settings.read_text()

# Reached by public IP, by ALB DNS and by the ALB health check.
text = re.sub(r"ALLOWED_HOSTS\s*=\s*\[[^\]]*\]", "ALLOWED_HOSTS = ['*']", text, count=1)

# Template directory required by the lab. The quoting and spacing of the
# generated settings.py has changed between Django versions, so match loosely.
if "polls/templates/" not in text:
    text, hits = re.subn(r"""(['"]DIRS['"]\s*:\s*)\[\s*\]""",
                         r"\1['polls/templates/']", text, count=1)
    if not hits:
        print("    WARNING: could not patch TEMPLATES DIRS - check settings.py by hand")

# Also registers the app, so APP_DIRS template loading works as a fallback.
if not re.search(r"""['"]polls['"]""", text):
    text = re.sub(r"INSTALLED_APPS\s*=\s*\[", "INSTALLED_APPS = [\n    'polls',", text, count=1)

settings.write_text(text)
print("    ALLOWED_HOSTS, TEMPLATES DIRS and INSTALLED_APPS updated")
PY

"${VENV}/bin/python3" manage.py migrate --noinput >/dev/null 2>&1 || true

# ------------------------------------------------------------------ nginx
step "Configuring nginx to proxy to 127.0.0.1:${DJANGO_PORT}"
cat > /etc/nginx/sites-enabled/default <<EOF
server {
  listen 80 default_server;
  listen [::]:80 default_server;

  location / {
    proxy_set_header X-Forwarded-Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;

    proxy_pass http://127.0.0.1:${DJANGO_PORT};
  }
}
EOF

nginx -t
systemctl restart nginx
systemctl enable nginx >/dev/null 2>&1 || true

# -------------------------------------------------- development server
# The lab runs "manage.py runserver 8000" in the foreground; a unit file keeps
# it up after the SSH session closes and across the ALB health checks.
step "Running the development server on port ${DJANGO_PORT} in the background"
cat > "/etc/systemd/system/${SERVICE}.service" <<EOF
[Unit]
Description=CITS5503 Lab 6 Django development server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${PROJECT}
Environment=PYTHONUNBUFFERED=1
Environment=AWS_DEFAULT_REGION=${AWS_REGION}
ExecStart=${VENV}/bin/python3 manage.py runserver ${DJANGO_PORT}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE}" >/dev/null 2>&1 || true
systemctl restart "${SERVICE}"

# ------------------------------------------------------------ smoke test
step "Smoke test"
for _ in $(seq 1 20); do
  CODE="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${DJANGO_PORT}/polls/" || true)"
  [[ "${CODE}" == "200" ]] && break
  sleep 2
done
echo "    django  http://127.0.0.1:${DJANGO_PORT}/polls/  -> ${CODE:-no response}"

CODE="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1/polls/" || true)"
echo "    nginx   http://127.0.0.1/polls/          -> ${CODE:-no response}"

if [[ "${CODE}" != "200" ]]; then
  echo
  echo "    Last 20 lines of the ${SERVICE} journal:"
  journalctl -u "${SERVICE}" -n 20 --no-pager | sed 's/^/      /'
  exit 1
fi

curl -s "http://127.0.0.1/polls/" | sed 's/^/      /' | head -n 25
echo
echo "Remote setup complete."
