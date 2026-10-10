# Lab 5 - Application Load Balancer across two Availability Zones

Builds a small load-balanced web tier in the default VPC: two EC2 instances in
different Availability Zones, an internet-facing Application Load Balancer
across both, and Apache on each instance serving a page titled with the
instance's name, so you can watch requests alternate between them.

- `lab5_alb.py` (boto3) finds the default VPC and picks subnets in two
  distinct AZs. It then creates a security group (22 + 80), a key pair, two
  `t3.micro` instances (one per subnet, tagged at launch), a target group with
  a tuned health check, the ALB and an HTTP:80 listener, and registers both
  instances. Everything is idempotent, and every id, ARN and the ALB DNS name
  are written back to `lab.env`.
- `lab5_web.sh` waits for SSH on each instance, installs Apache, and rewrites
  the default page's `<title>` to the instance name.
- `lab5_verify.sh` checks instance types, the AZ split, ALB state and scheme,
  target health and each page title, then sends six requests through the ALB.

## AWS services

EC2 (VPC/subnet discovery, security groups, key pairs, instances), Elastic
Load Balancing v2 (ALB, target group, listener), STS.

## Configuration

Required in `lab.env`: `LAB_PREFIX`, `AWS_REGION`, `AWS_AMI`. Written back:
`LAB5_*` names and ids, `KEY_NAME`, `KEY_PATH`, `SSH_USER`.

## Run

```bash
labenv
python3 lab5_alb.py       # ~3-4 min, mostly waiting for the ALB
./lab5_web.sh             # ~2-3 min, installs Apache over SSH
./lab5_verify.sh
```

Needs outbound SSH (22) and HTTP (80) from your network. See the
troubleshooting section in the top-level README.

## Cost and cleanup

**The load balancer bills per hour whether or not it serves traffic**, on top
of two instances and their public IPv4 addresses. Delete the ALB first, then
the target group, then terminate both instances; release any Elastic IPs (none
are allocated by these scripts). The security group allows SSH and HTTP from
`0.0.0.0/0`, so don't leave it attached to anything long-lived.

See [NOTES.md](NOTES.md) for gotchas I hit along the way.
