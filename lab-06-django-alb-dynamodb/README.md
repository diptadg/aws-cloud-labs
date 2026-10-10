# Lab 6 - Django + nginx on EC2 behind an ALB, reading DynamoDB

Deploys a small Django app to an EC2 instance, puts nginx in front of it,
fronts the instance with an Application Load Balancer, and has the app render
the file records from Lab 3, copied from DynamoDB Local into a real DynamoDB
table.

- `lab6_ec2.py` creates the security group, key pair (reusing Lab 5's if its
  `.pem` is still on disk) and a `t3.micro` instance.
- `lab6_ddb_sync.py` creates a `UserFiles` table (on-demand billing, same key
  schema as Lab 3) and copies the records into it from DynamoDB Local. If the
  Lab 3 data or bucket is gone, `--from-s3` or `--from-tree` rebuild the same
  six attributes.
- `lab6_deploy.sh` waits for SSH, stages your AWS credentials on the instance
  for the app, and runs `lab6_remote_setup.sh` there. That builds a venv,
  creates the Django project and `polls` app, writes a view that scans the
  table, configures nginx as a reverse proxy to `127.0.0.1:8000`, and keeps the
  dev server running under a systemd unit.
- `lab6_alb.py` creates a target group with a `/polls/` health check every
  30 s, the ALB across two AZs and the listener, then waits for the target to
  report healthy.
- `lab6_verify.sh` checks the instance, page, health check, ALB, both tables,
  and that every record appears on the page served through the ALB.

## AWS services

EC2, Elastic Load Balancing v2, DynamoDB (AWS and Local), S3 (`--from-s3`
only), STS.

## Configuration

Required in `lab.env`: `LAB_PREFIX`, `AWS_REGION`, `AWS_AMI`. Optional:
`KEY_NAME`, `KEY_PATH`, `SSH_USER`, `DDB_TABLE`, `DDB_PORT`, `AWS_DDB_TABLE`
(default `UserFiles`), `DJANGO_PORT` (default 8000), `BUCKET_NAME`,
`ROOT_DIR`; `LAB6_TREE` in the environment. Written back: `LAB6_*`.

## Run

```bash
labenv
python3 lab6_ec2.py
python3 lab6_ddb_sync.py        # needs DynamoDB Local from Lab 3, or --from-tree
./lab6_deploy.sh
python3 lab6_alb.py
./lab6_verify.sh
```

## Security note

`lab6_deploy.sh` copies `~/.aws/credentials` to `/root/.aws` on the instance
so the app can reach DynamoDB. That is a long-lived key on a public host. In
anything beyond a short lab, attach an IAM instance role with read access to
the one table instead. Django runs with the development server and
`ALLOWED_HOSTS = ['*']`; this is a lab deployment, not a production one.

## Cost and cleanup

**The ALB and the instance bill per hour**, the DynamoDB table per request.
Delete the ALB, then the target group, terminate the instance, delete the
`UserFiles` table, and deactivate the access key you staged on the instance if
it was ever exposed.

See [NOTES.md](NOTES.md) for gotchas I hit along the way.
