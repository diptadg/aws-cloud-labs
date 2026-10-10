# Lab 7 - Automated Django deployment with Fabric

Turns Lab 6's manual server setup into one repeatable Fabric run that takes a
bare Ubuntu instance to a working `http://<ip>/polls/` page.

- `lab7_prepare.sh` installs Fabric into the venv and pre-flights what breaks
  on WSL2: key location and mode, CRLF line endings in the key and scripts,
  outbound TCP 22, and clock drift measured against AWS's `Date` header.
- `lab7_ec2.py` creates the security group, key pair and a `t3.micro`
  instance, choosing a subnet only in a real Availability Zone that offers the
  instance type (filtering out the Auckland Local Zone). `--cleanup`
  terminates it for a fresh practice run.
- `lab7_sshconfig.py` re-reads the instance's current address from EC2 and
  writes a managed `Host` block into `~/.ssh/config`, so `Connection('<name>')`
  needs no other arguments.
- `lab7_fabric.py` is the deployment. It waits for cloud-init, retries apt
  while dpkg is locked, then builds the venv, Django project and `polls` app,
  writes nginx's reverse-proxy config, starts the dev server in the background
  (systemd unit by default, `--runner nohup` for setsid/nohup), and smoke-tests
  from both the instance and your machine.
- `lab7_verify.sh` checks the instance, SSH config, listening ports, nginx's
  effective config and the page, then lists anything left running.

The script works around a few Fabric pitfalls, each commented in place:
`c.sudo()` is not a shell (redirects and `cd` need `bash -c`), uploads go
through `/tmp` then `install` because SFTP runs as the login user, and a
naive `nohup ... &` keeps the SSH channel open forever.

## AWS services

EC2 (instances, security groups, key pairs, AZ and instance-type offerings,
Elastic IPs in cleanup).

## Configuration

Required in `lab.env`: `LAB_PREFIX`, `AWS_REGION`, `AWS_AMI`. Optional:
`KEY_NAME`, `KEY_PATH`, `SSH_USER`, `DJANGO_PORT`, `LAB7_SSH_CONFIG`.
Written back: `LAB7_*`.

## Run

```bash
labenv
./lab7_prepare.sh
python3 lab7_ec2.py
python3 lab7_sshconfig.py
python3 lab7_fabric.py          # --runner nohup, --restart-only, --upgrade
./lab7_verify.sh                # --cleanup: only list leftovers
```

## Cost and cleanup

**One instance billing by the hour**, plus its public IPv4 address. Run
`python3 lab7_ec2.py --cleanup` (terminates it and releases unassociated
Elastic IPs), or terminate it in the console. The security group and key pair
are left for the next run; delete them when finished.

See [NOTES.md](NOTES.md) for gotchas I hit along the way.
