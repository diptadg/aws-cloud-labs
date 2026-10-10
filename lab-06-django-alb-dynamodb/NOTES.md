# CITS5503 Lab 6 — Web Application

> Working notes written while doing this lab, aimed at the in-lab demo and its
> checkpoints. Identifiers are placeholders (`<prefix>` = `LAB_PREFIX`), and paths
> assume the scripts were copied into `~/cits5503/labN/`. Start with
> [README.md](README.md) for the overview.

Django + nginx on EC2, fronted by an Application Load Balancer, reading the
CloudStorage file list out of AWS DynamoDB.

## Files

| File | What it does |
|---|---|
| `lab6_env.py` | lab.env reader/writer used by the Python scripts |
| `lab6_lib.sh` | lab.env reader/writer and helpers used by the shell scripts |
| `lab6_ec2.py` | Security group (22 + 80), key pair, one `t3.micro` instance `<prefix>-vm1` |
| `lab6_ddb_sync.py` | Copies the Lab 3 DynamoDB Local records into an AWS DynamoDB table |
| `lab6_deploy.sh` | Waits for SSH, stages credentials, runs the remote setup |
| `lab6_remote_setup.sh` | Runs **on the instance**: venv, Django, `polls` app, nginx, dev server |
| `lab6_alb.py` | Target group (`/polls/` health check, 30 s), ALB, listener, registration |
| `lab6_verify.sh` | Read-only rehearsal of every checkpoint. Creates and deletes nothing |

## Run order

```bash
mkdir -p ~/cits5503/lab6
# copy these files there, then:
cd ~/cits5503/lab6
chmod +x *.sh

labenv                          # the shared venv — boto3 lives there

python3 lab6_ec2.py             # ~1 min
python3 lab6_ddb_sync.py        # needs DynamoDB Local from Lab 3 running
./lab6_deploy.sh                # ~3-4 min, most of it apt and pip
python3 lab6_alb.py             # ~3-4 min, mostly waiting for the ALB
./lab6_verify.sh                # confirm everything before you demo
```

Every script is idempotent — re-running is safe and reuses what already exists.
`./lab6_deploy.sh` in particular is the thing to re-run if the app misbehaves;
it rewrites the app files, the nginx config and the service, then restarts both.

If the Lab 3 local data is gone, there are two fallbacks:

```bash
python3 lab6_ddb_sync.py --from-s3      # the bucket still exists
python3 lab6_ddb_sync.py --from-tree    # the bucket was deleted at cleanup
```

Both write the records to DynamoDB Local **and** to AWS DynamoDB, so the live
local scan in Checkpoint 3 still has something to return.

`--from-tree` touches S3 not at all. It reads the local `rootdir/` tree —
looking in this directory, then `../lab3/`, then `~/cits5503/lab3/`, and
recreating it with the Lab 3 contents if none of those exist — and takes the
owner identity from your account's canonical S3 owner via `list_buckets`, which
works with no buckets present. The resulting items carry the same six
attributes Lab 3 defines, so the local scan and the web page look identical to
the bucket-backed version. Point it somewhere else with
`LAB6_TREE=/path/to/rootdir`.

This is the normal case, not an edge case: Lab 3 and Lab 4 both end by deleting
the bucket, and Lab 6 is not assessed on it. What Checkpoint 3 wants is the two
tables agreeing with what the page renders.

## Resources created

Names are built from `LAB_PREFIX` in `lab.env`, so nothing below is typed
into a script.

| Resource | Name |
|---|---|
| Security group | `<prefix>-sg-lab6` (inbound tcp/22 and tcp/80) |
| Key pair | `<prefix>-key`, private key at `~/.ssh/<prefix>-key.pem` |
| Instance | `<prefix>-vm1` — `t3.micro`, default VPC |
| Target group | `<prefix>-tg-lab6` — HTTP:80, health check `/polls/` every 30 s |
| Load balancer | `<prefix>-alb-lab6` — internet-facing, two subnets |
| Listener | HTTP:80 → forward to the target group |
| DynamoDB table | `UserFiles` in your assigned region |

On the instance: `/opt/wwc/mysites/myvenv`, the `lab` project with the `polls`
app at `/opt/wwc/mysites/lab`, nginx proxying `:80` → `127.0.0.1:8000`, and a
`django-polls` systemd unit running `manage.py runserver 8000`.

## Configuration

Everything is read from `~/cits5503/lab.env` — the same file Labs 1–5 use. No
script carries a fallback for an identity value: a missing key stops the script
with a message naming it. Set `LAB_ENV=/some/path` to point at a different file.

**Read from Labs 1–5:** `LAB_PREFIX`, `AWS_REGION`, `AWS_AMI`,
`BUCKET_NAME`, `ROOT_DIR`, `DDB_TABLE`, `DDB_PORT`, `KEY_NAME`, `KEY_PATH`,
`SSH_USER`. `BUCKET_NAME` is only read by `--from-s3`, so a deleted bucket
costs nothing. Existing values are reused rather than duplicated under new names —
the Lab 5 key pair is picked up as-is if it is still on disk.

**Written by Lab 6:** `LAB6_SG_ID`, `LAB6_SG_NAME`, `LAB6_VPC_ID`,
`LAB6_SUBNET_ID`, `LAB6_VM1_NAME`, `LAB6_VM1_ID`, `LAB6_VM1_IP`,
`LAB6_VM1_DNS`, `LAB6_VM1_AZ`, `LAB6_TG_NAME`, `LAB6_TG_ARN`, `LAB6_ALB_NAME`,
`LAB6_ALB_ARN`, `LAB6_ALB_DNS`, `LAB6_LISTENER_ARN`, `AWS_DDB_TABLE`. Plus
`KEY_NAME`, `KEY_PATH` and `SSH_USER` if Lab 5 did not already record them.

The values that stay literal in the scripts are the ones the lab fixes:
`t3.micro`, the `ubuntu` login user, `/opt/wwc/mysites`, the `/polls/` health
check path, the 30 second interval, and the `userId`/`fileName` key schema.

`DJANGO_PORT` (default `8000`) and `AWS_DDB_TABLE` (default `UserFiles`) can be
overridden from the environment or from `lab.env` if you want different values.

## Checkpoints

**Checkpoint 1 — EC2, Django and nginx.** In the console show EC2 → Instances
with `<prefix>-vm1` running, type `t3.micro`, the region matching `AWS_REGION`,
and a public IPv4 address. Then open
`http://<instance-public-ip>/polls/` in a browser tab.

**Checkpoint 2 — Application Load Balancer.** Open `lab6_alb.py` and show
`ensure_load_balancer()` — the `create_load_balancer` call with the two subnets,
the security group, `Scheme="internet-facing"` and `Type="application"`. Then in
the console, EC2 → Target Groups → `<prefix>-tg-lab6`: the Targets tab shows
the instance registered with status **Healthy**, and the Health checks tab shows
path `/polls/` and interval `30` seconds.

**Checkpoint 3 — AWS DynamoDB.** Open `lab6_ddb_sync.py` and show
`ensure_table()`, which creates the AWS table with `userId` as the partition key
and `fileName` as the sort key. Then, with DynamoDB Local running:

```bash
aws dynamodb scan \
  --table-name CloudFiles \
  --endpoint-url http://localhost:8000 \
  --output table
```

Then open `http://<alb-dns-name>/polls/` and show that the listed file names are
the ones the scan returned. `lab6_verify.sh` compares the two automatically, so
there are no surprises.

**Checkpoint 4 — Cleanup.** In the AWS Management Console, in this order:

1. EC2 → Load Balancers → `<prefix>-alb-lab6` → Actions → Delete
   (then EC2 → Target Groups → `<prefix>-tg-lab6` → Delete)
2. EC2 → Instances → `<prefix>-vm1` → Instance state → Terminate
3. DynamoDB → Tables → `UserFiles` → Delete
4. EC2 → Elastic IPs → release anything allocated

Delete the load balancer first so the listener is not left pointing at an
instance that no longer exists. Nothing here allocates an Elastic IP, so step 4
is normally an empty list — still open the page so the facilitator can see it.

## Credentials on the instance

The Django view scans AWS DynamoDB, so the instance needs credentials. The lab
text pastes the access key and secret straight into `views.py`; `lab6_deploy.sh`
copies `~/.aws/credentials` to `/root/.aws/credentials` on the instance instead
and lets the normal boto3 credential chain find them. Same effect, but the keys
are not sitting in a source file that ends up on screen during the demo.

It is still a long-lived key on a public host. Terminate the instance as soon as
the cleanup checkpoint is done, and treat the key as exposed if the instance is
ever left running.

## Gotchas for this setup

**Port 80 on the UWA network.** The university network commonly blocks outbound
HTTP to arbitrary hosts, so both the instance page and the ALB DNS can look dead
while everything is configured correctly. Test on a phone hotspot or your home
connection before assuming something is broken. `lab6_verify.sh` says so
whenever a `curl` fails.

**Keep the `.pem` on the Linux filesystem.** `lab6_ec2.py` writes it to
`~/.ssh/`. A key on `/mnt/c/...` gets DrvFs permissions that SSH refuses, and
`chmod 400` there is a silent no-op.

**WSL clock drift.** After Windows sleeps, AWS calls fail with an expired
signature. Fix with `sudo hwclock -s`, then re-run.

**Do not run these with `sudo`.** They only need your user. Running them as root
leaves root-owned files in `~/cits5503` and breaks the venv on the next run. If
it happens: `sudo chown -R "$USER:$USER" ~/cits5503`.

**DynamoDB Local and the Django server both use 8000.** They never collide —
DynamoDB Local is on your laptop, Django is on the EC2 instance. Only the local
one is reachable at `localhost:8000`.

**The health check hits `/polls/` every 30 seconds.** The Django development
server logs each one, so `journalctl -u django-polls -f` on the instance is a
quick way to confirm the ALB is really probing it.

**Target unhealthy after a redeploy.** It takes about a minute for two
consecutive successful checks to flip the state back to healthy. Re-run
`./lab6_verify.sh` rather than assuming the registration is wrong.
