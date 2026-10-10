# CITS5503 Lab 2 — scripts

> Working notes written while doing this lab, aimed at the in-lab demo and its
> checkpoints. Identifiers are placeholders (`<prefix>` = `LAB_PREFIX`), and paths
> assume the scripts were copied into `~/cits5503/labN/`. Start with
> [README.md](README.md) for the overview.

Everything is driven by `~/cits5503/lab.env`. No prefix, region, AMI or
resource name is hardcoded in any script.

| File | What it does |
|---|---|
| `lab_env.sh` | Shared `lab.env` loader/writer — sourced by the shell scripts |
| `lab2_awscli.sh` | Lab steps [1]–[7] with the AWS CLI → `<prefix>-vm1` |
| `lab2_boto3.py` | Lab steps [1]–[6] with boto3 → `<prefix>-vm2` |
| `lab2_docker.sh` | html/ + Dockerfile scaffold, builds the image, runs the container |
| `lab2_verify.sh` | Read-only rehearsal of all three checkpoints |

## Setup

```bash
mkdir -p ~/cits5503/lab2
cp lab_env.sh lab2_*.sh lab2_boto3.py README_lab2.md ~/cits5503/lab2/
cd ~/cits5503/lab2
sudo chown -R "$USER:$USER" ~/cits5503
chmod +x lab_env.sh lab2_awscli.sh lab2_docker.sh lab2_verify.sh lab2_boto3.py
```

## Run order

```bash
labenv                    # activate the Lab 1 venv, where boto3 lives
./lab2_awscli.sh          # Checkpoint 1 instance
python3 lab2_boto3.py     # Checkpoint 2 instance
./lab2_docker.sh up       # httpd container → http://localhost
./lab2_verify.sh          # rehearse before demonstrating
./lab2_docker.sh down     # docker stop + docker rm
```

Then terminate both instances **in the AWS Management Console** — the console
action is what Checkpoint 3 asks for, and doing it from the CLI leaves nothing
to demonstrate.

## What lab.env supplies

Each script prints what it read and whether each value came from the file or
fell back to a default, so a stale or truncated `lab.env` is visible
immediately rather than surfacing later as a confusing AWS error.

**Required** — the scripts refuse to run without them:

```bash
LAB_PREFIX="<prefix>"
AWS_AMI="ami-xxxxxxxxxxxxxxxxx"
```

**Read if present, defaulted otherwise:**

| Key | Falls back to |
|---|---|
| `AWS_REGION` (or `AWS_LAB_REGION`) | `ap-southeast-2` |
| `AWS_AMI` (or `AWS_LAB_AMI`) | none - required |
| `INSTANCE_TYPE` | `t3.micro` |
| `SG_NAME`, `KEY_NAME`, `VM1_NAME`, `KEY_FILE` | `<prefix>-sg`, `-key`, `-vm1`, `~/.ssh/<key>.pem` |
| `SG_NAME_BOTO`, `KEY_NAME_BOTO`, `VM2_NAME`, `KEY_FILE_BOTO` | `<prefix>-sg-boto`, `-key-boto`, `-vm2` |
| `DOCKER_IMAGE`, `DOCKER_CONTAINER`, `DOCKER_PORT` | `my-apache2`, `my-app`, `80` |

Alternate key names are accepted throughout (`AWS_LAB_REGION`, `AWS_DEFAULT_REGION`,
`AMI_ID`, and so on), so a file written by a different lab script still works.

**Written back** after a successful run: `SG_ID`, `VM1_ID`, `VM1_IP`,
`VM2_ID`, `VM2_IP` and the boto3 resource names. Existing keys are updated in
place — comments, ordering and unrelated keys are preserved, so nothing gets
truncated the way it was before.

Value formats all parse identically, and can be mixed in one file:

```bash
KEY=value        KEY="value"       KEY='value'
export KEY="v"   KEY = value       KEY="value"  # trailing comment
```

Each value's source (lab.env or default) is printed at start-up, so a typo or a
missing key is visible before anything is created.

## Things that bite on this setup

**The lab sheet's `run-instances` command fails as written.** It passes
`--security-group-ids <prefix>-sg`, but that flag wants the
`sg-xxxxxxxx` id, not the group name. Both scripts capture the id from
`create-security-group` and pass that. (`--group-name` is fine for
`authorize-security-group-ingress`, which is why step [2] works.)

**Skip the Docker install section entirely.** Docker Desktop with WSL
integration already puts `docker` on your PATH inside Ubuntu.
`sudo apt install docker.io` would install a second engine that fights over
`/var/run/docker.sock`, and `sudo systemctl start docker` has nothing to start —
the engine runs in the `docker-desktop` WSL distro, not your Ubuntu one.
`lab2_docker.sh` checks `docker info` instead.

**Port 80 on Windows.** `http.sys`, IIS, or the World Wide Web Publishing
Service sometimes reserves it, and the bind fails with a socket error that
doesn't mention Windows at all. Either `./lab2_docker.sh up --port 8080` or
`DOCKER_PORT="8080"` in `lab.env` sidesteps it; the container still listens on
80 internally.

**Keep the `.pem` on the Linux filesystem.** `~/.ssh/<key>.pem` is correct. A
key under `/mnt/c/...` inherits DrvFs permissions and `ssh` rejects it as
world-readable no matter how many times you `chmod 400`.

**Don't run the scripts with `sudo`.** They'd write `lab.env` and your `.pem` as
root, and your normal shell then can't read the key to SSH in. `sudo` is only
needed for the one-off `chown` above.

**WSL clock drift.** After Windows sleeps, the clock skews and every AWS call
dies with an expired-signature error that reads exactly like bad credentials.
`sudo hwclock -s` fixes it.

**Ubuntu 26.04 specifics.** Python is well past the 3.8/3.10 the sheet
recommends — a floor, not a ceiling. The system interpreter is externally
managed, so boto3 stays in the Lab 1 venv; activate it with `labenv`. If you
forget, `lab2_boto3.py` finds the venv (`labenv`, `.venv`, `venv`, or
`$VIRTUAL_ENV`) and re-executes inside it rather than dying on the import.

## Idempotency

Re-running either AWS script reuses the existing security group, key pair and
instance rather than launching duplicates. If AWS holds a key pair whose `.pem`
you've lost, the scripts offer to delete and recreate it, since AWS cannot
re-issue a private key.

## During the demo

Checkpoint 2 asks you to open the boto3 source and point at where the instance
is created and named: `step4_run_instance()` and `step5_tag_instance()` in
`lab2_boto3.py`. You don't need to rerun the script.
