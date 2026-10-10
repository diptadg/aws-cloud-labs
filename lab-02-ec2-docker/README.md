# Lab 2 - EC2 with the AWS CLI and boto3, plus Docker

Launches the same kind of EC2 instance twice, once with AWS CLI commands and
once with boto3, then builds and runs a small Apache container locally.

Each EC2 script creates a security group that allows SSH, a key pair (private
key saved to `~/.ssh` with mode 400), a `t3.micro` instance from your AMI, and
a `Name` tag, then waits for the public IP and prints the SSH command. Both are
idempotent: a re-run reuses the security group, key pair and instance rather
than creating duplicates. If AWS still has the key pair but the `.pem` is gone,
they offer to recreate it. If the `.pem` survives but AWS lost the key pair,
they re-import its public half instead of overwriting the only working key.

`lab2_docker.sh` scaffolds an `httpd:2.4` image that serves a one-line page,
runs it, and checks the response. It works with Docker Desktop's WSL
integration, and if Windows has reserved port 80 it tells you to retry with
`--port 8080`.

| File | Purpose |
|---|---|
| `lab_env.sh` | shared loader: parses `lab.env` and writes ids back into it |
| `lab2_awscli.sh` | EC2 instance `<prefix>-vm1` with the AWS CLI |
| `lab2_boto3.py` | EC2 instance `<prefix>-vm2` with boto3 |
| `lab2_docker.sh` | `up` / `build` / `status` / `logs` / `down` / `clean` for the container |
| `lab2_verify.sh` | read-only check of both instances and the container |

## AWS services

EC2 (security groups, key pairs, instances, tags), STS.

## Configuration

Required in `lab.env`: `LAB_PREFIX`, `AWS_AMI`. `AWS_REGION` defaults to
`ap-southeast-2`. Optional: `INSTANCE_TYPE` (default `t3.micro`), the
`SG_NAME*` / `KEY_NAME*` / `VM*_NAME` / `KEY_FILE*` names, and
`DOCKER_IMAGE` / `DOCKER_CONTAINER` / `DOCKER_PORT`. The scripts write
`SG_ID`, `VM1_ID`, `VM1_IP`, `VM2_ID` and `VM2_IP` back into the file.

## Run

```bash
labenv
./lab2_awscli.sh            # --status to inspect, --ssh to connect afterwards
python3 lab2_boto3.py       # same options
./lab2_docker.sh up         # then open http://localhost (or --port 8080)
./lab2_verify.sh
./lab2_docker.sh down
```

## Cost and cleanup

**Two running EC2 instances bill by the hour**, plus their public IPv4
addresses. Terminate both instances when you are done (console, or
`aws ec2 terminate-instances`), then delete the two key pairs and security
groups if you won't reuse them. The Docker part runs locally and costs nothing.

See [NOTES.md](NOTES.md) for gotchas I hit along the way.
