# Lab 5 - Networking (ALB + 2 EC2 instances)

> Working notes written while doing this lab, aimed at the in-lab demo and its
> checkpoints. Identifiers are placeholders (`<prefix>` = `LAB_PREFIX`), and paths
> assume the scripts were copied into `~/cits5503/labN/`. Start with
> [README.md](README.md) for the overview.

Environment assumed: Windows 11 + WSL2 (Ubuntu 26.04).

## Configuration

Every setting is read from `~/cits5503/lab.env`. Nothing is hard-coded in the
scripts and there are no fallback defaults — if the file is missing or a key is
empty, the script stops with a message naming the key. The file must contain at
least:

```bash
LAB_PREFIX="<prefix>"
AWS_REGION="ap-southeast-2"
AWS_AMI="ami-xxxxxxxxxxxxxxxxx"
```

`t3.micro` is the one value not taken from the file: the lab mandates it, so it
is a constant in `lab5_alb.py` rather than something that can drift.

`lab5_alb.py` writes its results back into the same file (`LAB5_SG_ID`,
`LAB5_VM1_ID`, `LAB5_ALB_ARN`, `LAB5_ALB_DNS`, `KEY_NAME`, `KEY_PATH`, …), which
is how `lab5_web.sh` and `lab5_verify.sh` find everything.

## Files

| File | What it does |
|---|---|
| `lab5_alb.py` | Boto3: security group (22 + 80), key pair, two `t3.micro` instances in two AZs, target group, internet-facing ALB, target registration, HTTP:80 listener |
| `lab5_web.sh` | SSH to each instance, `apt-get update`, install `apache2`, rewrite the `<title>` tag to the instance name |
| `lab5_verify.sh` | Read-only rehearsal of every checkpoint. Creates and deletes nothing |

## Run order

```bash
cp lab5_alb.py lab5_web.sh lab5_verify.sh ~/cits5503/
cd ~/cits5503
chmod +x lab5_web.sh lab5_verify.sh

labenv                 # activate the venv - boto3 lives there
python3 lab5_alb.py    # ~3-4 min, most of it waiting for the ALB to go active
./lab5_web.sh          # ~2-3 min, waits for SSH then installs Apache2
./lab5_verify.sh       # confirm everything before you demo
```

All three scripts are idempotent, so re-running them is safe. Resource IDs are
written back into `~/cits5503/lab.env` under `LAB5_*` keys.

## Resources created

Names are built from `LAB_PREFIX` in `lab.env` (shown below as it resolves
for `<prefix>`).

| Resource | Name |
|---|---|
| Security group | `<prefix>-sg-lab5` → `<prefix>-sg-lab5` |
| Key pair | `<prefix>-key` → `<prefix>-key` (private key at `~/.ssh/<prefix>-key.pem`) |
| Instances | `<prefix>-vm1`, `<prefix>-vm2` (both `t3.micro`, different AZs) |
| Target group | `<prefix>-tg` (HTTP:80, target type `instance`) |
| Load balancer | `<prefix>-alb` (internet-facing, application, two subnets) |
| Listener | HTTP:80 -> forward to `<prefix>-tg` |

## Checkpoints

**Checkpoint 1 - EC2 and ALB.** In the console show EC2 > Instances with both
`<prefix>-vm1` and `<prefix>-vm2` running, both `t3.micro`, and the
Availability Zone column showing two different AZs. Then EC2 > Load Balancers,
select `<prefix>-alb`, and show State = Active, Scheme = internet-facing, and
the two mapped subnets/AZs.

**Checkpoint 2 - Workload balancing.** Open `http://<vm1-public-ip>/` and
`http://<vm2-public-ip>/` in a browser. The browser tab title shows the
instance name for each. Zoom the browser or hover the tab so the title is
readable. `lab5_verify.sh` prints both titles beforehand so there are no
surprises.

**Checkpoint 3 - Cleanup.** Done in the AWS Management Console, in this order:

1. EC2 > Load Balancers > select `<prefix>-alb` > Actions > Delete
2. EC2 > Instances > select both instances > Instance state > Terminate
3. EC2 > Elastic IPs > release anything allocated

The order matters: deleting the load balancer first avoids leaving a listener
pointing at instances that no longer exist. Nothing in these scripts allocates
an Elastic IP, so step 3 will normally show an empty list - still open the page
so the facilitator can see it.

## Gotchas specific to this setup

**Port 80 on the UWA network.** The university network commonly blocks outbound
HTTP to arbitrary hosts, so the instance pages and the ALB DNS may look dead
even when everything is configured correctly. Test on a phone hotspot or home
connection before the demo.

**Keep the `.pem` on the Linux filesystem.** `~/.ssh/<prefix>-key.pem` works;
a copy under `/mnt/c/...` gets DrvFs permissions that `chmod 400` cannot fix,
and OpenSSH rejects it with "Permissions are too open". `lab5_alb.py` writes
the key to `~/.ssh` for this reason.

**Reusing the Lab 2 key pair.** If `<prefix>-key` still exists in AWS but the
`.pem` is gone, AWS cannot re-issue the private key. Delete the key pair with
`aws ec2 delete-key-pair --key-name <prefix>-key` and re-run `lab5_alb.py` to
get a fresh one - safe as long as no instance you still need depends on it.

**Clock drift after Windows sleep.** Expired-signature or
`RequestExpired` errors from AWS almost always mean the WSL clock has drifted.
Fix with `sudo hwclock -s`, then re-run.

**Never use `sudo` on these scripts.** Running them as root leaves root-owned
files in `~/cits5503` and breaks the venv. If it happens:
`sudo chown -R $USER:$USER ~/cits5503`.

**Targets look unhealthy at first.** The target group health check hits `/` over
HTTP, which fails until Apache2 is installed. Both targets flip to `healthy`
roughly 30 seconds after `lab5_web.sh` finishes.

**Security group naming.** A separate group `<prefix>-sg-lab5` is used rather
than the Lab 2 group, because this one also needs inbound HTTP. The scripts
always pass the `sg-xxxxxxxx` ID to `--security-group-ids` / `GroupId`, never
the group name, which is what the lab sheet's Lab 2 command gets wrong.

**Docker isn't involved in this lab.** Nothing here needs Docker Desktop
running.

## Audit note - what is and isn't taken from `lab.env`

Read from the file (script fails loudly if absent): `LAB_PREFIX`,
`AWS_REGION`, `AWS_AMI`.

Written to the file by `lab5_alb.py` and then read back by the shell scripts:
`LAB5_SG_NAME`, `LAB5_VM1_NAME`, `LAB5_VM2_NAME`, `LAB5_TG_NAME`,
`LAB5_ALB_NAME`, `LAB5_INSTANCE_TYPE`, `LAB5_SG_ID`, `LAB5_VPC_ID`,
`LAB5_SUBNET_1/2`, `LAB5_VM1_ID`, `LAB5_VM2_ID`, `LAB5_TG_ARN`, `LAB5_ALB_ARN`,
`LAB5_ALB_DNS`, `KEY_NAME`, `KEY_PATH`, `SSH_USER`.

Deliberately constant, because the lab sheet fixes them:

| Constant | Value | Why |
|---|---|---|
| `INSTANCE_TYPE` | `t3.micro` | mandated; making it configurable only creates a way to fail Checkpoint 1 |
| `LISTENER_PORT` | `80` | the listener rule is specified as HTTP on port 80 |
| `SSH_PORT` | `22` | SSH ingress |
| `INGRESS_CIDR` | `0.0.0.0/0` | the lab authorises inbound from anywhere |
| `SSH_USER` | `ubuntu` | login user for the Ubuntu AMI (also written to lab.env so the shell scripts share it) |
| Name suffixes | `-sg-lab5`, `-key`, `-vm1`, `-vm2`, `-tg`, `-alb` | `-vm1`/`-vm2` are the required naming format; the rest are defined once in `lab5_alb.py` and exported |

The `${VAR:-...}` fallbacks in the shell scripts only matter if you run them
before `lab5_alb.py`, and every one of them derives from `LAB_PREFIX`
rather than embedding a number.

Health-check tuning (15s interval, 5s timeout, 2 healthy / 2 unhealthy) and the
SSH retry budget (30 attempts, 10s apart) are behavioural, not identity, so they
stay in the scripts.

`LAB_ENV=/path/to/file` overrides the config location on all three scripts.
