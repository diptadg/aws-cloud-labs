# Lab 8 - Jupyter on ECS Fargate, SageMaker hyperparameter tuning

Packages a Jupyter server in a Docker image, pushes it to ECR, runs it as an
ECS Fargate service with a public IP, and from the notebook inside that
container launches a SageMaker XGBoost hyperparameter tuning job against a
public bank-marketing dataset.

- `Dockerfile` takes the base image, port, token, notebook URL and SageMaker
  SDK version as build arguments. It pins the SDK below v3 (v3 moved
  `image_uris` and pulls in several GB of CUDA libraries), skips pip's cache,
  and refuses to build without a token.
- `lab8_prepare.sh` checks Docker, CLI v2, boto3, WSL2 DNS and clock drift,
  then writes every Lab 8 name into `lab.env`, generating a random Jupyter
  token on first run.
- `lab8_image.sh` builds the image and records its architecture, so the ECS
  task definition's `runtimePlatform` matches. `--test` runs it locally,
  `--slim` builds on `python:3.10-slim`, and `--diagnose` works out why a page
  is blank. `lab8_docker_doctor.sh` diagnoses a build stuck at "exporting
  layers".
- `lab8_ecr.py` creates the repository; `lab8_push.sh` logs in with
  `--password-stdin` (not `-p`, which leaks the token into history), tags and
  pushes.
- `lab8_ecs.py` creates the security group, picks three subnets in real AZs
  (Local Zones filtered out), an optional CloudWatch log group, the task
  definition, cluster and service, waits for stability and resolves the task's
  public IP. `lab8_ip.sh` does the same lookup with the AWS CLI.
- `lab8_bucket.py` creates the bucket and prefix the tuning job uses.
  `lab8_notebook.py` writes a copy of the course notebook with your bucket,
  prefix and role filled in, and a credentials cell that tries the task role
  before prompting.
- `lab8_verify.sh` checks every stage. `lab8_cleanup.py --all` tears it down in
  dependency order, retrying the security group until ECS releases the ENI.

## AWS services

ECR, ECS (Fargate, task definitions, services), EC2 (security groups,
subnets, ENIs), IAM (task and execution roles), SageMaker (hyperparameter
tuning, training jobs), S3, CloudWatch Logs, STS.

## Prerequisites

Docker Desktop with WSL integration, and two IAM roles that already exist in
the account: `SageMakerRole` (ECS task role, also passed to SageMaker) and
`ecsTaskExecutionRole`. Override the names with `LAB8_TASK_ROLE` /
`LAB8_EXEC_ROLE`.

## Configuration

Required in `lab.env`: `LAB_PREFIX`, `AWS_REGION`. Everything else is seeded
by `lab8_prepare.sh` (see `config.example.env`), including
`LAB8_JUPYTER_TOKEN` and `LAB8_NOTEBOOK_URL`. The course notebook itself is
not in this repo: `LAB8_NOTEBOOK_URL` must point at a copy you have access to.

## Run

```bash
labenv
./lab8_prepare.sh
./lab8_image.sh --test          # build and test locally; --stop afterwards
python3 lab8_ecr.py
./lab8_push.sh
python3 lab8_bucket.py
python3 lab8_ecs.py
python3 lab8_notebook.py        # upload the result through Jupyter, run it
./lab8_verify.sh
python3 lab8_cleanup.py --all
```

## Security note

The notebook is served over plain HTTP on a public IP. The token is baked into
the image's environment, so anyone who can pull the image can read it. Prefer
the task role over typing access keys into the notebook, and delete the
service as soon as the tuning job finishes.

## Cost and cleanup

Several meters run here: **the Fargate task per second while the service
exists, two `ml.m5.xlarge` training jobs, ~2 GB of ECR storage, S3, CloudWatch
Logs** and the task's public IPv4 address. Run `python3 lab8_cleanup.py --all`
(ECR, ECS, security group, S3, log group) and confirm in the console. Completed
tuning jobs cannot be deleted, but they cost nothing once finished.

See [NOTES.md](NOTES.md) for gotchas I hit along the way.
