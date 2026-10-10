# aws-cloud-labs

Python (boto3, Fabric) and Bash scripts from nine hands-on AWS labs I did for
the Cloud Computing unit (CITS5503) at the University of Western Australia.
Over the nine labs they go from CLI setup to S3/DynamoDB, KMS, load-balanced
EC2, a Django deployment automated with Fabric, containers on ECS Fargate,
SageMaker tuning, and Comprehend/Rekognition.

Every lab follows the same conventions:

- **Idempotent.** Re-running a script reuses what already exists instead of
  creating duplicates.
- **One config file.** Names, region and AMI come from `lab.env`, and nothing
  account-specific is hard-coded. Scripts write the ids they create back into
  the same file for later steps.
- **Read-only verify scripts.** Each lab has a `*_verify.sh` that checks the
  result and changes nothing.
- **Cleanup documented.** Each lab README says what to delete, and the later
  labs have a cleanup script.

They were written for and run on Windows 11 + WSL2 (Ubuntu) in
`ap-southeast-2`, which is why WSL2 issues appear throughout.

> Only my own code and notes are here. The unit's lab sheets, specifications
> and supplied files (including the Lab 8 notebook and the Lab 9 images) are
> not included.

## Labs

| Lab | What it builds | AWS services |
|---|---|---|
| [01 CLI setup](lab-01-cli-setup/) | AWS CLI v2, boto3 venv, credentials, region listing | EC2 (describe), STS, IAM access keys |
| [02 EC2 + Docker](lab-02-ec2-docker/) | EC2 instance via CLI and via boto3; local Apache container | EC2, STS |
| [03 S3 + DynamoDB](lab-03-s3-dynamodb/) | Directory backup/restore to S3; file metadata in DynamoDB Local | S3, DynamoDB Local |
| [04 KMS + encryption](lab-04-kms-encryption/) | Single-user bucket policy; KMS and AES-256 encrypt/decrypt | S3, KMS, IAM, STS |
| [05 ALB multi-AZ](lab-05-alb-multi-az/) | Two instances in two AZs behind an ALB | EC2, ELBv2 |
| [06 Django + ALB + DynamoDB](lab-06-django-alb-dynamodb/) | Django/nginx on EC2 behind an ALB, data from DynamoDB | EC2, ELBv2, DynamoDB, S3 |
| [07 Fabric deploy](lab-07-fabric-deploy/) | Lab 6's server setup as one Fabric run | EC2 |
| [08 ECS Fargate + SageMaker](lab-08-ecs-fargate-sagemaker/) | Jupyter image in ECR on Fargate; XGBoost tuning job | ECR, ECS Fargate, IAM roles, SageMaker, S3, CloudWatch Logs |
| [09 Comprehend + Rekognition](lab-09-comprehend-rekognition/) | Language/sentiment/entity analysis; image labels, faces, text | Comprehend, Rekognition, S3 |

`tools/lab_fixperms.sh` repairs ownership, permissions and CRLF line endings
after files have been copied in from Windows or a script was run with `sudo`.

## Setup

Requirements: WSL2 with Ubuntu (or any Linux with bash), Python 3.10+, an AWS
account with an IAM user and access key, and Docker Desktop with WSL
integration for Labs 2, 3 and 8.

```bash
git clone https://github.com/diptadg/aws-cloud-labs.git
cd aws-cloud-labs

# 1. Tooling, venv, credentials and the config file (prompts for each value)
./lab-01-cli-setup/lab1_setup.sh
#    or by hand:
#      python3 -m venv ~/cits5503/.venv && ~/cits5503/.venv/bin/pip install -r requirements.txt
#      mkdir -p ~/cits5503 && cp config.example.env ~/cits5503/lab.env  # then edit it
#      aws configure

# 2. Activate the venv in each new shell
source ~/cits5503/.venv/bin/activate      # or: labenv

# 3. Work through a lab from its folder
cd lab-05-alb-multi-az && cat README.md
```

The config file is `~/cits5503/lab.env` by default; set `LAB_ENV=/path` to use
another. Three values are required: `LAB_PREFIX` (resource-name prefix),
`AWS_REGION` (default `ap-southeast-2`) and `AWS_AMI` (an Ubuntu AMI in that
region). [`config.example.env`](config.example.env) documents every other key.
Credentials stay in `~/.aws`, never in this repo or in `lab.env`.

> **These scripts create billable resources.** Every lab README has a cost and
> cleanup section. The load balancers (Labs 5-6), the KMS key (Lab 4) and the
> Fargate service and training jobs (Lab 8) are the ones that keep charging if
> forgotten.

## Troubleshooting notes

Real problems I hit doing these labs on WSL2.

### AWS signature errors after Windows sleeps

- **Symptom:** after the laptop wakes, every AWS call fails with
  `InvalidSignatureException`, `SignatureDoesNotMatch` or "Signature expired",
  which looks like a credentials problem.
- **Cause:** WSL2's clock does not resync when Windows resumes, so it drifts
  minutes behind and AWS rejects the request timestamps.
- **Fix:** `sudo hwclock -s`. Several prepare scripts measure the drift
  against the `Date` header from S3 and warn before anything runs.

### `chmod 600` silently does nothing for `~/.aws` or `.pem` files

- **Symptom:** `chmod 600` or `chmod 400` reports success, but `ls -l` still
  shows open permissions, and `ssh` refuses the key with "UNPROTECTED PRIVATE
  KEY FILE" / "permissions are too open".
- **Cause:** the files are on the Windows filesystem (`/mnt/c/...`, or `~/.aws`
  symlinked there). DrvFs doesn't store Linux permission bits by default, so
  chmod is a no-op.
- **Fix:** keep `~/.aws` and keys on the Linux filesystem (`~/.aws`,
  `~/.ssh/*.pem`), copy them over if needed, and `chmod` there. The scripts
  write keys to `~/.ssh` and warn if a key path starts with `/mnt/`.

### SSH and HTTP blocked on the UWA network

- **Symptom:** `ssh` to a new instance times out, Fabric can't connect, and
  `http://<public-ip>/` never loads, while the same check run on the instance
  itself (`curl http://127.0.0.1/`) works.
- **Cause:** the campus network blocks outbound TCP 22 and 80 to arbitrary
  hosts.
- **Fix:** use a phone hotspot or another network for those steps.
  `lab7_prepare.sh` tests outbound 22 up front, and the deploy scripts smoke-test
  on the instance first, so a network block can be told apart from a broken
  deployment.

### `apt install awscli` gives CLI v1

- **Symptom:** `aws --version` prints `aws-cli/1.x`, and some commands and
  flags behave differently from the v2 documentation.
- **Cause:** Ubuntu's `awscli` package is the old v1 CLI.
- **Fix:** `sudo apt remove awscli`, then install v2 with Amazon's zip
  installer (`lab1_setup.sh` does this). Several prepare and verify scripts
  flag v1.

### `apt install docker.io` conflicts with Docker Desktop

- **Symptom:** after installing Docker inside Ubuntu, `docker` commands hit the
  wrong engine or fail on the socket, and `sudo systemctl start docker`
  doesn't help.
- **Cause:** Docker Desktop's WSL integration already provides the `docker`
  CLI and runs the engine in its own distro. `docker.io` adds a second daemon
  competing for `/var/run/docker.sock`.
- **Fix:** `sudo apt remove docker.io`, then enable this distro under Docker
  Desktop > Settings > Resources > WSL integration and open a new shell. The
  scripts check `docker info`, not systemd.

### The Auckland Local Zone gets picked as an Availability Zone

- **Symptom:** in `ap-southeast-2`, a script that picks subnets automatically
  fails at launch: `RunInstances` errors such as
  `VolumeTypeNotAvailableInZone`, or Fargate tasks that never start with a
  capacity-style error that looks like a quota problem.
- **Cause:** the region also exposes the Auckland Local Zone
  (`ap-southeast-2-akl-1a`). Its subnets come back from `describe_subnets`
  like any other, but it doesn't offer `t3.micro` or Fargate.
- **Fix:** filter `describe_availability_zones` by
  `zone-type=availability-zone` before choosing subnets, as `lab7_ec2.py` and
  `lab8_ecs.py` do (Lab 7 also checks `describe_instance_type_offerings`).

## Repository layout

```
lab-0N-*/          scripts for each lab, a README, and my original NOTES.md
tools/             lab_fixperms.sh
config.example.env every configuration key, with defaults
requirements.txt   local Python dependencies (boto3, pycryptodome, fabric)
```
