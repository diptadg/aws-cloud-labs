# CITS5503 Lab 8 - Container and AI

> Working notes written while doing this lab, aimed at the in-lab demo and its
> checkpoints. Identifiers are placeholders (`<prefix>` = `LAB_PREFIX`), and paths
> assume the scripts were copied into `~/cits5503/labN/`. Start with
> [README.md](README.md) for the overview.

Build a Jupyter image, push it to ECR, run it on ECS Fargate, and launch a
SageMaker hyperparameter tuning job from the notebook inside that container.

Prefix `<prefix>`, region `ap-southeast-2`, Windows 11 + WSL2 (Ubuntu
26.04) with Docker Desktop.

---

## Files

| File | Role |
|---|---|
| `lab8_env.py` | lab.env reader/writer for the Python scripts |
| `lab8_lib.sh` | lab.env loader/writer for the shell scripts |
| `lab8_prepare.sh` | local checks (Docker, CLI v2, boto3, DNS, clock) and the only writer of the Lab 8 name keys |
| `Dockerfile` | the lab sheet's Dockerfile, with token/port/notebook URL as build arguments |
| `lab8_image.sh` | build the image; `--test` runs it locally, `--slim` builds a much smaller one, `--diagnose` explains a blank page |
| `lab8_ecr.py` | create or check the ECR repository, decode the authorisation token |
| `lab8_push.sh` | log in to ECR, tag, push |
| `lab8_ecs.py` | security group, subnets, task definition, cluster, service, public IP |
| `lab8_ip.sh` | the lab sheet's AWS CLI chain for the public IP |
| `lab8_bucket.py` | create the S3 bucket and prefix the tuning job needs |
| `lab8_notebook.py` | write a copy of `LabAI.ipynb` with your prefix and bucket already filled in; `--source` accepts a local file or a URL |
| `LabAI_<prefix>.ipynb` | that filled copy, ready to upload (generated, git-ignored) |
| `lab8_docker_doctor.sh` | diagnose a build that looks stuck, especially at "exporting layers" |
| `lab8_verify.sh` | read-only rehearsal of every stage |
| `lab8_cleanup.py` | delete ECS, the security group, ECR, S3 and the log group, in dependency order |

---

## Order to run

```bash
labenv                       # the shared Lab 1 virtualenv

./lab8_prepare.sh            # checks, and seeds lab.env
./lab8_image.sh --test       # build, then confirm Jupyter serves on 127.0.0.1:8888
                             # add --slim if the build stalls on disk
./lab8_image.sh --stop       # stop the local test container

python3 lab8_ecr.py          # create the repository, print the login command
./lab8_push.sh               # log in, tag, push (the slow step)

python3 lab8_bucket.py       # create <prefix>-lab8 for the tuning job
python3 lab8_ecs.py          # deploy, wait for stable, print the public IP
./lab8_ip.sh                 # the CLI form of the same lookup

python3 lab8_notebook.py     # write the pre-filled notebook

# open http://<public ip>:8888/?token=<LAB8_JUPYTER_TOKEN> and run the notebook

./lab8_verify.sh             # rehearse before the demo
python3 lab8_cleanup.py --all
```

---

## The four checkpoints, and what to have ready

### Checkpoint 1 - Docker image

In your terminal:

```bash
docker image ls | grep <prefix>-lab8
```

Then, with the local test container running (`./lab8_image.sh --test` starts it
and leaves it up), open `http://127.0.0.1:8888`, enter your `LAB8_JUPYTER_TOKEN` at
the prompt, and show `LabAI.ipynb` in the file listing.

Note the checkpoint wants you to *log in with the token*, not to arrive already
authenticated. Use the bare `http://127.0.0.1:8888` address rather than the
`?token=` URL, so the login form appears.

`./lab8_image.sh --test` verifies both halves of this before you demo: that the
image serves, and that `LabAI.ipynb` was actually fetched during the build. That
`wget` is the step that fails silently if the build had no network.

### Checkpoint 2 - ECR and ECS

Console → Elastic Container Registry → Repositories → `<prefix>_ecr_repo`.

Console → Elastic Container Service → Clusters → `<prefix>-cluster` → Services:
`<prefix>-service`, status Active.

Console → same cluster → Tasks, on the running task show that it uses Fargate,
that its **Image URI matches the ECR repository and tag** from the first step,
and that it has a public IP.

Then `http://<task public ip>:8888`, log in with the token, show `LabAI.ipynb`.

`lab8_verify.sh` checks the task's own `launchType` and `containers[0].image`
against `LAB8_IMAGE_URI`, not just the service, because the image URI match is
the specific thing being looked at. A task can be running happily off a stale
task definition revision pointing at an older image.

### Checkpoint 3 - SageMaker hyperparameter tuning

Console → Amazon SageMaker AI → Hyperparameter tuning jobs →
`<prefix>-xgboost-tuningjob-01`, status Completed.

Inside it, the Training jobs tab: two jobs, both Completed, both named starting
with your prefix. Those names are derived from the tuning job name, so
they follow automatically as long as `LAB8_TUNING_JOB` is right.

Console → S3 → `<prefix>-lab8` → `sagemaker/<prefix>-hpo-xgboost-dm/output`:
two folders, each named with your prefix, each containing
`model.tar.gz`.

`lab8_verify.sh` checks the counts exactly — two folders, two training jobs —
rather than just "not empty", because a partially failed tuning job leaves one
of each and still looks fine at a glance.

### Checkpoint 4 - Cleanup

The checkpoint asks for the console, in order: ECR, then ECS, then empty and
delete S3. `python3 lab8_cleanup.py --all` follows the same order if you would
rather do it from the terminal, then show the empty console views.

### Two copies of the notebook are in circulation

The unit hands `LabAI.ipynb` out twice: the raw GitHub URL the Dockerfile
`wget`s, and a file attached to the lab sheet. They are not identical. All
sixteen code cells are byte-for-byte the same; the sheet's copy fixes a typo and
drops the two dataset questions that the GitHub copy asks. The container gets
the GitHub copy, so those questions will be in the notebook you open.

`lab8_notebook.py` defaults to the URL and takes `--source` for a local file:

```bash
python3 lab8_notebook.py --source ~/Downloads/LabAI.ipynb
```


---

## lab.env contract

Everything configurable lives in `~/cits5503/lab.env`. No script carries a
fallback for an identity value: a missing key stops the script and names the
key. `LAB_ENV=/some/other/path` overrides the location on every script.

**Read from Labs 1-7, never re-written by Lab 8**

`LAB_PREFIX`, `AWS_REGION`.

`AWS_ACCOUNT_ID` is written only if no earlier lab recorded it
(`set_if_absent`), because it is an account fact rather than a Lab 8 value.

**Written by `lab8_prepare.sh`, read by everything else**

| Key | Value |
|---|---|
| `LAB8_IMAGE_NAME` | `<prefix>-lab8` |
| `LAB8_IMAGE_TAG` | `latest` |
| `LAB8_ECR_REPO` | `<prefix>_ecr_repo` |
| `LAB8_BUCKET` | `<prefix>-lab8` |
| `LAB8_CLUSTER` | `<prefix>-cluster` |
| `LAB8_SERVICE_NAME` | `<prefix>-service` |
| `LAB8_TASK_FAMILY` | `<prefix>-task-family` |
| `LAB8_CONTAINER` | `<prefix>-container` |
| `LAB8_SG_NAME` | `<prefix>-sg-lab8` |
| `LAB8_TUNING_JOB` | `<prefix>-xgboost-tuningjob-01` |
| `LAB8_S3_PREFIX` | `sagemaker/<prefix>-hpo-xgboost-dm` |
| `LAB8_JUPYTER_PORT` | `8888` |
| `LAB8_JUPYTER_TOKEN` | random, generated on first run |
| `LAB8_TASK_ROLE` | `SageMakerRole` |
| `LAB8_EXEC_ROLE` | `ecsTaskExecutionRole` |
| `LAB8_TASK_CPU` / `LAB8_TASK_MEMORY` | `256` / `512` |
| `LAB8_NOTEBOOK` / `LAB8_NOTEBOOK_URL` | `LabAI.ipynb` and its raw GitHub URL |
| `LAB8_BASE_IMAGE` | `python:3.10` (`--slim` switches to `python:3.10-slim`) |
| `LAB8_SAGEMAKER_SPEC` | `sagemaker>=2.200,<3` |
| `LAB8_LOG_GROUP` | `/ecs/<prefix>-task-family` |

**Written by the script that discovers them**

| Key | Writer |
|---|---|
| `LAB8_IMAGE_ARCH` | `lab8_image.sh`, from `docker image inspect` |
| `LAB8_ECR_URI`, `LAB8_ECR_REGISTRY` | `lab8_ecr.py` |
| `LAB8_IMAGE_URI` | `lab8_push.sh` |
| `LAB8_VPC_ID`, `LAB8_SUBNET_IDS`, `LAB8_SG_ID`, `LAB8_TASK_DEF_ARN`, `LAB8_LOGS_ENABLED` | `lab8_ecs.py` |
| `LAB8_PUBLIC_IP`, `LAB8_JUPYTER_URL` | `lab8_ecs.py` and `lab8_ip.sh` |

Change the prefix in `lab.env`, re-run `./lab8_prepare.sh`, and every
resource name follows.

### What is deliberately literal

- **Port 443** in `lab8_ecs.py`. HTTPS is a fixed number, not a setting.
- **`0.0.0.0/0`** on the inbound rule. The lab needs the notebook reachable from
  your browser and there is no fixed campus address to narrow it to.
- **`desiredCount` 1, `FARGATE`, `awsvpc`, `assignPublicIp` ENABLED** in
  `lab8_ecs.py`. All specified by the lab sheet.
- **Three subnets** (`MAX_SUBNETS`). The lab sheet asks for three.
- **`us-east-1`** in `lab8_bucket.py`. That region rejects an explicit
  `LocationConstraint`; it is a fact about S3, not configuration.
- **The `amd64`/`arm64` to `X86_64`/`ARM64` map** in `lab8_ecs.py`. A fact about
  how Docker and ECS spell the same thing.

---

## Details worth knowing before something breaks

### The image architecture has to match Fargate

An image built on Apple Silicon is ARM64 and will not start on the default
x86_64 Fargate platform; the task dies at pull time with an exec-format error
that reads like a corrupt image. `lab8_image.sh` records what Docker actually
built in `LAB8_IMAGE_ARCH`, and `lab8_ecs.py` sets `runtimePlatform` from it.
On WSL2 this will be `amd64`, but the mechanism is there either way, and
`lab8_verify.sh` fails loudly if the recorded value and the built image drift
apart.

### The port lives in one place

The lab sheet's Dockerfile has `8888` in `EXPOSE` and again in `CMD`, and then
you type it into the security group and the port mapping. Four copies. Here the
`CMD` is in shell form so it can expand `${JUPYTER_PORT}` at run time, and the
build argument, the security group rule, the container port mapping and the URL
in `lab8_ip.sh` all come from `LAB8_JUPYTER_PORT`.

Shell form does mean Jupyter runs as a child of `/bin/sh` rather than as PID 1.
That does not matter here - ECS stops the task rather than sending it signals to
handle.

### The ECR login password

The lab sheet has you print `docker login -u AWS -p <password> <registry>` and
paste it. That puts a live 12-hour credential in your shell history and in the
process list where any other user on the machine can read it. `lab8_push.sh`
pipes the same password into `docker login --password-stdin` instead.
`python3 lab8_ecr.py` still prints the lab sheet's version, and
`./lab8_push.sh --show-cmd` prints just that line, if you would rather run it by
hand.

One small fix to the lab sheet's decoder: it does
`base64.b64decode(token).decode().split(':')`, which raises `ValueError` if the
password ever contains a colon. `lab8_ecr.py` uses `split(':', 1)`.

### Local Zones are not Availability Zones

`ap-southeast-2` exposes `ap-southeast-2-akl-1a`, the Auckland Local Zone. Its
subnets come back from `describe_subnets` like any other, but Fargate does not
run there, and the failure surfaces as a capacity error that reads like a quota
problem. `pick_subnets()` filters `zone-type=availability-zone` explicitly.

### Credentials in the notebook

The notebook's first cell asks you to type an access key and a secret key into a
browser session that is talking to a public IP over plain HTTP. Nothing about
that connection is encrypted.

The task already carries `SageMakerRole` as its task role, so boto3 inside the
container can get credentials from the ECS container metadata endpoint without
being told anything. The notebook `lab8_notebook.py` generates tries that first
and only prompts if it fails. If you do end up typing keys, treat them as
exposed: delete the service as soon as the tuning job finishes, and rotate them.

### `No module named 'sagemaker.image_uris'`

An unpinned `pip install sagemaker` now installs SDK **v3** (3.0 shipped in
November 2025). Two consequences, one visible and one not:

- **v3 moved `image_uris`** from `sagemaker.image_uris` to
  `sagemaker.core.image_uris`, so the notebook's
  `from sagemaker.image_uris import retrieve` fails.
- **v3 depends on torch and about twenty NVIDIA CUDA packages** - 173 packages
  resolved, against 62 for v2. That is several gigabytes of GPU libraries in a
  CPU-only container, and it is the real reason the build crawled at
  "exporting layers" and why the push is so slow.

The notebook only uses the SDK for that one `retrieve()` call - everything else
goes through boto3 - so pinning to v2 costs nothing:

- the Dockerfile installs `LAB8_SAGEMAKER_SPEC` (`sagemaker>=2.200,<3`)
- `lab8_notebook.py` pins the notebook's own `!pip3 install` cell to the same
  spec, so it cannot pull v3 over the top of the image's v2
- the generated import tries the v2 path and falls back to the v3 path, so the
  notebook runs whichever SDK happens to be installed

Both paths return the same URI,
`<sagemaker-registry-account>.dkr.ecr.<region>.amazonaws.com/xgboost:latest`
(an AWS-owned registry account, not mine).

**Unblocking a running notebook without a rebuild:** change that import line to
`from sagemaker.core.image_uris import retrieve`. That is enough for the tuning
job; rebuild with the pin afterwards to get the image size down.

### If a script stops with a missing key

```
LAB8_SOMETHING is missing or empty in ~/cits5503/lab.env.
```

Re-run `./lab8_prepare.sh`. It only fills in keys that are absent, so an
existing `lab.env` is left alone, and it is the only script that writes the
name keys. This happens when the suite gains a key after you first set up.

Two categories of key are deliberately *not* hard requirements:

- **Non-identity values with a sensible default** (`LAB8_BASE_IMAGE`,
  `LAB8_NOTEBOOK_URL`) use `lab_env_optional`, so an older `lab.env` still
  works.
- **Values a later stage discovers** (`LAB8_IMAGE_ARCH`, `LAB8_IMAGE_URI`,
  `LAB8_SG_ID`, `LAB8_PUBLIC_IP`) are reported by `lab8_verify.sh` as stage
  failures rather than stopping it. A partial run is what you want halfway
  through the lab.

Modes that do not build - `--stop`, `--diagnose` - do not check build-time keys
at all.

### A blank page at 127.0.0.1:8888

```bash
./lab8_image.sh --diagnose
```

It walks the four causes in order and stops at the first that matches:

1. **Nothing is running.** Building the image does not start a container.
   `./lab8_image.sh` on its own only builds; `--test` is what runs it.
2. **The container started and died.** The script prints the exit code and the
   last 30 log lines.
3. **Jupyter is up but the working directory is empty.** A blank *file listing*
   also reads as "blank page". It means the `ADD` step did not fetch the
   notebook during the build, usually because a cached layer was reused after a
   failed download. `docker build --no-cache` fixes it.
4. **Everything is fine server-side**, so it is the browser. Two usual
   suspects: typing `127.0.0.1:8888` without `http://`, which makes some
   browsers run a search instead; and a part-loaded JupyterLab bundle cached as
   a broken page, where a normal reload serves the same broken cache back.
   Ctrl+Shift+R, or an incognito window to confirm.

Jupyter also takes 10-20 seconds to start after the container does, so a page
opened immediately after `docker run` will be empty for a moment.

### When the build stalls at "exporting layers"

That is the final BuildKit step, after every `RUN` has finished: it is writing
the image into the store. On WSL2 it stalls for three reasons, and all three
look the same from the terminal - no error, no progress bar movement.

From a second WSL terminal, `./lab8_docker_doctor.sh --watch` shows whether the
numbers are still moving and prints the fix for each case. In short:

- **The Docker VM is out of disk.** `docker builder prune -af` then
  `docker system prune -a --volumes`. The build cache survives a cancelled
  build, so rebuilding after freeing space goes straight back to the export
  step instead of reinstalling anything.
- **The Windows drive is full.** The VM's disk is a `.vhdx` under
  `%LOCALAPPDATA%\Docker\wsl` that grows on demand; when C: runs out, writes
  stall rather than fail. Pruning inside Docker does not shrink the `.vhdx` -
  that needs `wsl --shutdown` then `Optimize-VHD`, or Docker Desktop →
  Settings → Troubleshoot → Clean / Purge data.
- **Defender is scanning the vhdx.** Excluding `%LOCALAPPDATA%\Docker` and the
  `com.docker.backend.exe`, `vmmem.exe` and `wslservice.exe` processes can turn
  a twenty-minute export back into two.

The Dockerfile here is already smaller than the lab sheet's: `pip` runs with
`--no-cache-dir` (the sheet's version leaves about half a gigabyte of wheel
cache inside the image), and the four `RUN` steps are collapsed into two, since
each one is a separate layer to compress and export.

If that is still too big, build on the slim base:

```bash
./lab8_image.sh --slim
```

`python:3.10` is roughly 1 GB before anything is installed; `python:3.10-slim`
is about 130 MB. Everything the lab needs installs on it as wheels, so nothing
is compiled and nothing is lost. This is why the Dockerfile uses `ADD` rather
than `wget` for the notebook - the slim base has no `wget`. The smaller image
also cuts the ECR push, which is the other slow step.

The choice is recorded in `LAB8_BASE_IMAGE`, so `./lab8_image.sh` on its own
keeps using whichever base you last built with.

### 512 MB is tight

The lab sheet's task definition asks for 256 CPU units and 512 MB, and the
notebook then does `pip3 install sagemaker pandas numpy` inside that container
and loads a 41,188-row dataframe. It usually fits, but if the task keeps
restarting or a cell dies with no traceback, that is the memory limit. Raise it
without touching any script:

```bash
python3 - <<'PY'
from lab8_env import update_env
update_env({"LAB8_TASK_CPU": "512", "LAB8_TASK_MEMORY": "2048"})
PY
python3 lab8_ecs.py          # re-registers the task definition and redeploys
```

Fargate only accepts particular CPU/memory pairs: 256 with 512/1024/2048, 512
with 1024 through 4096, 1024 with 2048 through 8192.

### Container logs

`lab8_ecs.py` creates `/ecs/<prefix>-task-family` and attaches an `awslogs`
driver, so a container that dies during startup leaves something behind:

```bash
aws logs tail /ecs/<prefix>-task-family --region ap-southeast-2 --since 20m
```

If the account denies `logs:CreateLogGroup` the script says so and carries on
without the log configuration rather than failing the deployment. `--no-logs`
skips it deliberately.

### The tuning job name has to be unique

SageMaker rejects a second job with the same name, and there is no delete API
for tuning jobs - a completed one stays in the account permanently. If you need
a second run, change `LAB8_TUNING_JOB` in `lab.env` (say to `...-tuningjob-02`)
and regenerate the notebook with `python3 lab8_notebook.py`.

### Cleanup order

The security group cannot be deleted while the task's network interface still
references it, and ECS detaches that interface asynchronously after the service
goes inactive. `lab8_cleanup.py --all` runs ECR, ECS, the security group, S3 and the log group
in that order - ECR/ECS/S3 is the order the cleanup checkpoint asks for, and the
security group is slotted in after ECS because of that dependency. It deletes
the service, waits on the `services_inactive` waiter, then retries the security
group deletion for two minutes against `DependencyViolation` before telling you
to try again.

S3 is the same shape: `delete_bucket` fails on a non-empty bucket, so the
objects and any version markers go first.

---

## WSL2 gotchas

**DNS breaks and the ECR push fails with "no such host".** The lab sheet calls
this out; `lab8_prepare.sh` checks for it before you waste a build on it.

```bash
sudo rm /etc/resolv.conf
echo "nameserver 8.8.8.8" | sudo tee /etc/resolv.conf
sudo chattr +i /etc/resolv.conf
```

Then `wsl --shutdown` from PowerShell and reopen the terminal. The `chattr +i`
is what stops WSL regenerating the file on the next boot.

**Clock drift after the laptop sleeps.** WSL2's clock does not resynchronise on
resume, and every AWS call then fails with a signature error that does not
mention time. `sudo hwclock -s`. `lab8_prepare.sh` compares against the `Date`
header from S3 and tells you the drift in seconds.

**Docker Desktop, not `apt install docker.io`.** Installing the distro package
inside WSL gives you a second daemon that fights the Docker Desktop integration.
Enable the distro under Docker Desktop -> Settings -> Resources -> WSL
integration instead.

**Disk.** The image is roughly 2 GB before layer overhead, and the push keeps a
compressed copy. `lab8_prepare.sh` warns below 15 GB free. If you are short,
`docker system prune -a` between the local test and the push.

**The UWA network.** Outbound 8888 is not reliably open on campus. If Jupyter
answers nothing from your browser but the ECS task is RUNNING and healthy, try a
phone hotspot before assuming the deployment is wrong.

---

## Verification

`./lab8_verify.sh` reads and never writes. Its five stages line up with the four
checkpoints: the Dockerfile, local image and its architecture; the ECR
repository and pushed tag; the cluster, service, task definition, running task
and security group; Jupyter answering on the live public IP with the notebook
present; and the tuning job with its training jobs and S3 artefacts.

A stale `LAB8_PUBLIC_IP` is reported as a failure, because a restarted task
comes back on a different address and that is the most common reason the browser
shows nothing.
