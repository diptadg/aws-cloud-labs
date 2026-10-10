# CITS5503 Lab 7 — DevOps

> Working notes written while doing this lab, aimed at the in-lab demo and its
> checkpoints. Identifiers are placeholders (`<prefix>` = `LAB_PREFIX`), and paths
> assume the scripts were copied into `~/cits5503/labN/`. Start with
> [README.md](README.md) for the overview.

Deploy Django + nginx onto an EC2 instance with Fabric, from a single script
run, ending at `http://<instance-public-ip>/polls/` serving `Hello, world.`

## Run order

```bash
mkdir -p ~/cits5503/lab7 && cd ~/cits5503/lab7   # copy the files here
chmod +x *.sh
labenv

./lab7_prepare.sh           # fabric into the venv, key/permission/network checks
python3 lab7_ec2.py         # sg (22+80), key pair, t3.micro <prefix>-vm1
python3 lab7_sshconfig.py   # ~/.ssh/config Host block, then the uname -s test
python3 lab7_fabric.py      # THE Fabric script: venv, Django, nginx, dev server
./lab7_verify.sh            # read-only rehearsal of all three checkpoints
```

Everything is idempotent. Re-running any script reuses what exists rather than
creating a second copy, so it is safe to run the whole sequence again if a step
fails partway.

## The files

| File | What it does |
|---|---|
| `lab7_env.py` | lab.env reader/writer for the Python scripts |
| `lab7_lib.sh` | lab.env loader for the shell scripts |
| `lab7_prepare.sh` | Installs Fabric into `labenv`; checks the key's location, mode and line endings, outbound port 22, and clock drift |
| `lab7_ec2.py` | Security group, key pair, `<prefix>-vm1`. `--cleanup` terminates it for a repeat practice run |
| `lab7_sshconfig.py` | Writes the `Host` block, re-reading the live address from EC2; runs the lab sheet's `Connection(...).run('uname -s')` test |
| `lab7_fabric.py` | **The deliverable.** venv, Django project, polls app, nginx proxy, dev server backgrounded on 8000 |
| `lab7_verify.sh` | Read-only rehearsal. `--cleanup` runs only the Checkpoint 3 leftover check |

## Configuration

Everything is read from `~/cits5503/lab.env` — the same file Labs 1–6 use. No
script carries a fallback for an identity value: a missing key stops the script
with a message naming it. Set `LAB_ENV=/some/path` to point at a different file.

**Read from Labs 1–6, reused as they stand:** `LAB_PREFIX`, `AWS_REGION`,
`AWS_AMI`, `KEY_NAME`, `KEY_PATH`, `SSH_USER`, `DJANGO_PORT`. Lab 7 adds no
second copy of any of these under a new name. If an earlier lab did not record
`KEY_NAME`, `KEY_PATH`, `SSH_USER` or `DJANGO_PORT`, `lab7_ec2.py` writes them
once and every later script reads them from there.

**Written by Lab 7:** `LAB7_SG_NAME`, `LAB7_SG_ID`, `LAB7_VPC_ID`,
`LAB7_SUBNET_ID`, `LAB7_VM_NAME`, `LAB7_VM_ID`, `LAB7_VM_IP`, `LAB7_VM_DNS`,
`LAB7_VM_AZ`, `LAB7_INSTANCE_TYPE`, `LAB7_SSH_CONFIG`, `LAB7_SERVICE`,
`LAB7_RUNNER`.

Ownership: `lab7_ec2.py` writes the resource keys, `lab7_sshconfig.py` refreshes
the address keys (`LAB7_VM_IP`, `LAB7_VM_DNS`) because a restarted instance comes
back on a new address, and `lab7_fabric.py` records which service name and
backgrounding mode the run actually used. `lab7_prepare.sh` and `lab7_verify.sh`
are read-only.

`LAB7_INSTANCE_TYPE`, `LAB7_SERVICE` and `LAB7_RUNNER` exist so `lab7_verify.sh`
checks what was actually created instead of carrying its own copies of
`t3.micro`, `django-polls` and the runner choice. That duplication was a real
bug in the Lab 5 verifier.

The values that stay literal in the scripts are the ones the lab itself fixes:
`t3.micro`, ports 22 and 80, `0.0.0.0/0`, `/opt/wwc/mysites`, `myvenv`, the
project name `lab`, the app name `polls`, `/etc/nginx/sites-enabled/default`,
and Django's own `manage.py runserver`. `lab7_prepare.sh` keeps fallbacks for
`KEY_NAME`/`KEY_PATH`/`SSH_USER` only because it legitimately runs before
`lab7_ec2.py` has written the real values; every later script requires them.

## Checkpoints

### Checkpoint 1 — Fabric script check

Open `lab7_fabric.py`. Three banner comments mark exactly what is asked for:

| Asked for | Function | Banner |
|---|---|---|
| Connecting to your EC2 instance | `connect()` | `CHECKPOINT 1 - part 1 of 3` |
| Configuring nginx to proxy requests to the Django development server | `configure_nginx()` | `CHECKPOINT 1 - part 2 of 3` |
| Launching the Django development server on port 8000 in the background | `start_django_in_background()` | `CHECKPOINT 1 - part 3 of 3` |

`./lab7_verify.sh` prints their current line numbers, so you can jump straight
there rather than scrolling:

```
== Checkpoint 1 - Fabric script
   ok    lab7_fabric.py present
         jump to these lines when asked:
           connecting to the instance        line 109  connect()
           nginx proxy to the dev server     line 262  configure_nginx()
           dev server on 8000, backgrounded  line 298  start_django_in_background()
```

Worth being able to say out loud for each one:

- **Connecting.** `Connection('<prefix>-vm1')` takes only the instance name.
  Hostname, user and identity file all come from the `Host` block in
  `~/.ssh/config`, which Fabric loads by default — this is the lab sheet's own
  example, wrapped in a retry loop for a freshly booted instance.
- **nginx.** `/etc/nginx/sites-enabled/default` is replaced with a server block
  listening on 80 that does `proxy_pass http://127.0.0.1:8000`, then `nginx -t`
  validates it and `systemctl restart nginx` applies it. The dev server binds to
  localhost only, so port 80 through nginx is the only way in.
- **Background.** A `django-polls` systemd unit runs
  `manage.py runserver 8000` with `Restart=always`. It outlives the Fabric run
  and the SSH session, and restarts itself if the server dies mid-demo.
  `--runner nohup` does the same thing with `setsid nohup` instead, if the
  facilitator wants to see literal backgrounding.

### Checkpoint 2 — Fabric automation

```bash
python3 lab7_fabric.py
```

Then open `http://<your-instance-public-ip>/polls/` in the browser. The page
shows `Hello, world.`

The script already makes that exact request itself at the end and prints the
response, so you know before you open the tab whether it will work. The URL is
printed on the last line, and `./lab7_verify.sh` prints it too.

### Checkpoint 3 — Cleanup

In the AWS Management Console:

1. EC2 → Instances → `<prefix>-vm1` → Instance state → Terminate instance
2. EC2 → Elastic IPs → release anything allocated

`./lab7_verify.sh --cleanup` lists whatever is still present in your region, so
you can confirm the console is showing everything before you say you are done.

For repeat practice runs before the demo, `python3 lab7_ec2.py --cleanup` does
the same thing from the terminal. The checkpoint itself has to be demonstrated
in the console.

## Things that will bite you

**The dev server must not block the Fabric run.** Written the obvious way,
`cd /opt/wwc/mysites/lab && nohup ... &` backgrounds the whole `cd && nohup`
list in a subshell whose stdout and stderr are still the SSH channel — so Fabric
waits for the development server to exit, which is never. The nohup path uses
`{ cd ...; } >> log 2>&1 < /dev/null &` instead, redirecting the group so no
stream stays attached. Measured: the naive form blocks for the full lifetime of
the child, the grouped form returns in hundredths of a second.

**`c.sudo()` is not a shell.** Fabric builds `sudo -S -p '<prompt>' <command>`
by string concatenation, so `c.sudo("cd /x && y")` runs `sudo cd` (exit 127) and
`c.sudo("echo hi > /etc/f")` does the redirect as `ubuntu`, not root. Anything
with `cd`, `&&`, a pipe or a redirect goes through the `sudo_sh()` helper, which
wraps it in `bash -c`. Config files go through `sudo_write()`, which stages in
`/tmp` and then `install`s as root, because Fabric's SFTP session is the
`ubuntu` user and `/opt/wwc` and `/etc` are root-owned.

**`pkill -f 'manage.py runserver'` kills its own shell.** The sudo command line
contains that pattern, so pkill matches it. Both the deploy script and the
verifier use `'[m]anage.py runserver'` — the bracket matches the server but not
the invocation carrying it.

**A restarted instance gets a new public IP.** `~/.ssh/config` then points at the
old one and Fabric hangs on connect. `lab7_sshconfig.py` re-reads the address
from EC2 every time rather than trusting lab.env, so re-running it fixes this;
`lab7_verify.sh` flags the mismatch explicitly.

**apt on a just-booted instance.** cloud-init and unattended-upgrades hold the
dpkg lock for the first minute or two. The script waits on
`cloud-init status --wait` and retries `apt-get` six times with a 15 second gap.

**`apt-get upgrade` is off by default.** The lab lists it, but it adds several
minutes and nothing the checkpoints need. `python3 lab7_fabric.py --upgrade`
includes it, with `--force-confold` so it cannot stop on a config prompt.

**UWA network.** Campus wifi commonly blocks outbound 22 and 80, which breaks
both the Fabric connection and the browser check. `lab7_prepare.sh` tests port
22 up front. If the on-instance smoke test passes but the check from your
machine fails, it is the network, not the deployment — use a phone hotspot.

**WSL2 specifics.** The `.pem` must live under `~/.ssh` on the Linux filesystem;
`chmod` is a silent no-op on `/mnt/c` and ssh will keep rejecting the key as too
open. If you downloaded these files through Windows, `dos2unix lab7_*` before
running anything, or bash reports `bad interpreter: /bin/bash^M`. After a Windows
sleep the WSL clock drifts and AWS returns signature errors — `sudo hwclock -s`.
`lab7_prepare.sh` checks all four.

**Fabric and the ssh config directives.** Fabric honours `Hostname`, `User`,
`Port` and `IdentityFile` from the `Host` block. It ignores
`UserKnownHostsFile` and `StrictHostKeyChecking`, but it uses paramiko's
`AutoAddPolicy` and never reads or writes `known_hosts`, so the effect is the
same — no host key prompt, no stale key error when an IP is recycled. Those two
directives still matter for the plain `ssh` calls `lab7_verify.sh` makes.

## Useful during the demo

```bash
# watch requests arrive as the marker loads the page
ssh -i ~/.ssh/<prefix>-key.pem ubuntu@<ip> 'journalctl -u django-polls -f'

# server died mid-demo, rebuild nothing, just restart it
python3 lab7_fabric.py --restart-only

# show literal backgrounding instead of a systemd unit
python3 lab7_fabric.py --runner nohup
```
