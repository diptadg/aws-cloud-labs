#!/usr/bin/env python3
"""
CITS5503 Lab 2 - Create an EC2 instance with Python Boto3

Implements lab sheet steps [1]-[6] using boto3 instead of the AWS CLI:

    [1] create_security_group          [4] run_instances  (t3.micro)
    [2] authorize_security_group_ingress (tcp/22)
    [3] create_key_pair                [5] create_tags    (Name=<prefix>-vm2)
                                       [6] describe_instances -> public IP

Step [7] (ssh) is optional and is offered with --ssh.
Step [8] (listing the instance in the AWS Management Console) is manual.

All resource names differ from the ones the awscli script creates, as the lab
sheet requires: -sg-boto, -key-boto, and the instance name -vm2.

Run it inside the Lab 1 virtual environment, which is where boto3 lives:

    labenv                  # the alias from Lab 1
    python3 lab2_boto3.py

If you forget, the script finds the venv itself and re-executes inside it.

Options:
    --prefix NAME        override LAB_PREFIX from ~/cits5503/lab.env
    --status             report what already exists and create nothing
    --ssh                wait for sshd and open a session (optional step 7)
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError, BotoCoreError
except ImportError:
    # boto3 is not on this interpreter. Rather than fail, look for the lab venv
    # and re-run ourselves inside it. Covers whatever it happens to be named -
    # labenv, .venv, venv - and $VIRTUAL_ENV if one is already active.
    _home = Path.home()
    _candidates = []
    if os.environ.get("VIRTUAL_ENV"):
        _candidates.append(Path(os.environ["VIRTUAL_ENV"]))
    _lab_root = Path(os.environ.get("LAB_DIR", _home / "cits5503"))
    for _root in (_lab_root, _home):
        for _name in ("labenv", ".venv", "venv", ".labenv", "env"):
            _candidates.append(_root / _name)

    for _venv in _candidates:
        _python = _venv / "bin" / "python3"
        if not _python.exists():
            _python = _venv / "bin" / "python"
        # Do NOT compare resolved paths here: a venv's bin/python3 is a symlink
        # back to the system interpreter, so resolve() makes them look identical.
        # A sentinel env var is what actually prevents an exec loop.
        if _python.exists() and not os.environ.get("LAB2_REEXEC"):
            _probe = subprocess.run(
                [str(_python), "-c", "import boto3"], capture_output=True
            )
            if _probe.returncode == 0:
                print(f"\033[2m    (re-running inside {_venv})\033[0m")
                os.environ["LAB2_REEXEC"] = "1"
                os.execve(
                    str(_python),
                    [str(_python), os.path.abspath(__file__), *sys.argv[1:]],
                    os.environ,
                )

    sys.exit(
        "boto3 is not available, and no lab virtual environment containing it "
        "was found.\n"
        "Activate it first (the Lab 1 alias):\n"
        "    labenv\n"
        "or point this script at it:\n"
        "    LAB_DIR=/path/to/dir/holding/the/venv python3 lab2_boto3.py"
    )

DEFAULT_REGION = "ap-southeast-2"
PREFIX_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,8}$")

LAB_DIR = Path(os.environ.get("LAB_DIR", Path.home() / "cits5503"))
LAB2_DIR = LAB_DIR / "lab2"
ENV_FILE = Path(os.environ.get("LAB_ENV_FILE") or os.environ.get("LAB_ENV") or LAB_DIR / "lab.env")

BLUE, GREEN, YELLOW, RED, DIM, RESET = (
    "\033[1;34m", "\033[1;32m", "\033[1;33m", "\033[1;31m", "\033[2m", "\033[0m"
)


def log(msg: str) -> None:   print(f"\n{BLUE}==> {msg}{RESET}")
def ok(msg: str) -> None:    print(f"    {GREEN}+{RESET} {msg}")
def warn(msg: str) -> None:  print(f"    {YELLOW}!{RESET} {msg}")
def info(msg: str) -> None:  print(f"    {DIM}{msg}{RESET}")
def die(msg: str) -> None:   sys.exit(f"\n{RED}ERROR:{RESET} {msg}\n")


def parse_lab_env(path: Path = ENV_FILE) -> dict[str, str]:
    """Parse lab.env into a dict.

    Accepts the same formats as lab_env.sh: quoted or bare values, an optional
    export prefix, spaces around '=', trailing comments and CRLF endings.
    """
    if not path.exists():
        return {}

    env: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.rstrip("\r").strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue

        value = value.strip()
        if value[:1] in ("'", '"'):
            quote = value[0]
            close = value.find(quote, 1)
            value = value[1:close] if close != -1 else value[1:]
        elif "#" in value:
            value = value.split("#", 1)[0]
        env[key] = value.strip()
    return env


def env_first(env: dict[str, str], *keys: str) -> str | None:
    """First non-empty value among the given aliases."""
    for key in keys:
        if env.get(key):
            return env[key]
    return None


def load_lab_env(override_prefix: str | None = None) -> dict[str, str]:
    """Resolve every setting this script needs, preferring lab.env over defaults."""
    env = parse_lab_env()
    if not env:
        die(
            f"lab.env not found at {ENV_FILE}\n"
            "  Run the Lab 1 setup script, or point at it:\n"
            f"     LAB_ENV_FILE=/path/to/lab.env python3 {Path(__file__).name}"
        )

    prefix = override_prefix or env_first(env, "LAB_PREFIX") or ""
    prefix = prefix.strip().strip("\"'").strip()
    if not PREFIX_RE.match(prefix):
        die(
            f"no valid LAB_PREFIX in {ENV_FILE} (got '{prefix or '<none>'}')\n"
            '  Expected a line like:  LAB_PREFIX="myname"   (2-9 chars: a-z, 0-9, -)'
        )

    region = env_first(env, "AWS_LAB_REGION", "AWS_REGION",
                       "AWS_DEFAULT_REGION", "LAB_REGION", "REGION")
    ami = env_first(env, "AWS_LAB_AMI", "AWS_AMI", "AMI_ID", "LAB_AMI", "IMAGE_ID", "AMI")
    if not ami:
        die(f"no AMI id in {ENV_FILE} - set AWS_AMI (see config.example.env).")
    sources = {
        "region": "lab.env" if region else "default",
        "AMI id": "lab.env",
    }
    region = region or DEFAULT_REGION

    warnings = []
    instance_type = env_first(env, "INSTANCE_TYPE", "AWS_LAB_INSTANCE_TYPE")
    sources["instance type"] = "lab.env" if instance_type else "default"
    instance_type = instance_type or "t3.micro"
    if instance_type != "t3.micro":
        warnings.append(f"instance type is '{instance_type}'; the lab requires t3.micro")

    cfg = {
        "prefix": prefix,
        "region": region,
        "ami": ami,
        "instance_type": instance_type,
        # Names must differ from the awscli run, as the lab sheet requires.
        "sg_name":  env_first(env, "SG_NAME_BOTO", "SG_NAME_2")   or f"{prefix}-sg-boto",
        "key_name": env_first(env, "KEY_NAME_BOTO", "KEY_NAME_2") or f"{prefix}-key-boto",
        "vm_name":  env_first(env, "VM2_NAME", "INSTANCE2_NAME")  or f"{prefix}-vm2",
    }
    cfg["key_file"] = env_first(env, "KEY_FILE_BOTO") or str(
        Path.home() / ".ssh" / f"{cfg['key_name']}.pem"
    )

    ok(f"Loaded {ENV_FILE} ({len(env)} keys)")
    for label, value in (
        ("prefix", prefix), ("region", region),
        ("AMI id", ami), ("instance type", instance_type),
    ):
        src = sources.get(label, "lab.env")
        print(f"      {label:<16} {value:<22} {DIM}({src}){RESET}")
    for message in warnings:
        warn(message)

    return cfg


def write_lab_env(key: str, value: str, path: Path = ENV_FILE) -> None:
    """Merge a key back into lab.env, updating in place and preserving comments."""
    if not path.exists():
        return
    lines = path.read_text().splitlines()
    replacement = f'{key}="{value}"'
    done = False
    for i, raw in enumerate(lines):
        probe = raw.rstrip("\r").lstrip()
        if probe.startswith("#") or "=" not in probe:
            continue
        if probe.startswith("export "):
            probe = probe[len("export "):].lstrip()
        if probe.partition("=")[0].strip() == key:
            lines[i] = replacement
            done = True
            break
    if not done:
        lines.append(replacement)
    path.write_text("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# Lookups - each returns None when the resource does not exist yet
# --------------------------------------------------------------------------- #
def find_security_group(ec2, name: str) -> str | None:
    resp = ec2.describe_security_groups(
        Filters=[{"Name": "group-name", "Values": [name]}]
    )
    groups = resp.get("SecurityGroups", [])
    return groups[0]["GroupId"] if groups else None


def key_pair_exists(ec2, name: str) -> bool:
    try:
        ec2.describe_key_pairs(KeyNames=[name])
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "InvalidKeyPair.NotFound":
            return False
        raise


def find_instance(ec2, name: str) -> dict | None:
    resp = ec2.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [name]},
            {"Name": "instance-state-name",
             "Values": ["pending", "running", "stopping", "stopped"]},
        ]
    )
    for reservation in resp["Reservations"]:
        for instance in reservation["Instances"]:
            return instance
    return None


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #
def step1_security_group(ec2, name: str) -> str:
    log("[1] Create a security group")
    existing = find_security_group(ec2, name)
    if existing:
        ok(f"Security group {name} already exists: {existing}")
        return existing

    response = ec2.create_security_group(
        GroupName=name,
        Description="security group for development environment",
    )
    group_id = response["GroupId"]
    ok(f"Created {name} -> {group_id}  (default VPC)")
    return group_id


def step2_authorize_ssh(ec2, group_id: str) -> None:
    log("[2] Authorise inbound traffic for ssh (tcp/22)")
    try:
        ec2.authorize_security_group_ingress(
            GroupId=group_id,
            IpPermissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 22,
                    "ToPort": 22,
                    "IpRanges": [
                        {"CidrIp": "0.0.0.0/0", "Description": "ssh from anywhere"}
                    ],
                }
            ],
        )
        ok("tcp/22 open to 0.0.0.0/0")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "InvalidPermission.Duplicate":
            ok("Port 22 is already open on this security group")
        else:
            raise


def step3_key_pair(ec2, name: str, key_file: Path) -> None:
    log("[3] Create a key pair")
    in_aws = key_pair_exists(ec2, name)

    if in_aws and key_file.exists():
        ok(f"Key pair {name} exists in AWS and {key_file} is present")
    elif in_aws and not key_file.exists():
        warn(f"AWS has key pair {name} but the private key is missing locally.")
        warn("AWS cannot re-issue a private key, so the pair must be recreated.")
        if input("    Delete and recreate it? [y/N] ").strip().lower() != "y":
            die("Cannot ssh without the .pem file.")
        ec2.delete_key_pair(KeyName=name)
        in_aws = False

    if not in_aws and key_file.exists():
        # The .pem survives but AWS lost the key name - usually deleted from the
        # console. Generating a fresh pair here would overwrite the .pem and
        # destroy the only key that opens any instance already running.
        warn(f"{key_file} exists but AWS has no key pair named {name}.")
        warn("Importing your existing key rather than generating a new one.")
        warn("(Running instances keep working - their authorized_keys is unchanged.)")
        try:
            public = subprocess.run(
                ["ssh-keygen", "-y", "-f", str(key_file)],
                capture_output=True, check=True,
            ).stdout
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            die(
                f"Could not derive a public key from {key_file}: {exc}\n"
                "  The file may be corrupt or not a private key."
            )
        ec2.import_key_pair(KeyName=name, PublicKeyMaterial=public)
        ok(f"Re-imported {name} - your existing {key_file} still works")

    elif not in_aws:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.parent.chmod(0o700)
        response = ec2.create_key_pair(KeyName=name)
        key_file.write_text(response["KeyMaterial"])
        ok(f"Private key written to {key_file}")

    key_file.chmod(stat.S_IRUSR)  # 400
    ok(f"Permissions on {key_file} set to 400")

    if str(key_file).startswith("/mnt/c/"):
        warn("This key lives on the Windows drive - ssh will reject its permissions.")


def step4_run_instance(ec2, ami: str, group_id: str, key_name: str,
                       instance_type: str) -> str:
    log("[4] Create the instance")
    response = ec2.run_instances(
        ImageId=ami,
        InstanceType=instance_type,       # from lab.env; t3.micro as required
        KeyName=key_name,
        SecurityGroupIds=[group_id],      # the sg-xxxxxxxx id, not the group name
        MinCount=1,
        MaxCount=1,
    )
    instance_id = response["Instances"][0]["InstanceId"]
    ok(f"Launched {instance_id} ({instance_type})")
    return instance_id


def step5_tag_instance(ec2, instance_id: str, vm_name: str) -> None:
    log("[5] Add a tag to your instance")
    ec2.create_tags(
        Resources=[instance_id],
        Tags=[{"Key": "Name", "Value": vm_name}],
    )
    ok(f"Name tag set to {vm_name}")


def step6_public_ip(ec2, instance_id: str) -> str:
    log("[6] Get the public IP address")
    info("waiting for the instance to reach 'running' ...")
    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])

    response = ec2.describe_instances(InstanceIds=[instance_id])
    instance = response["Reservations"][0]["Instances"][0]
    public_ip = instance.get("PublicIpAddress")
    if not public_ip:
        die("No public IP assigned - check the subnet auto-assigns public IPs.")
    ok(f"Public IP: {public_ip}")
    return public_ip


# --------------------------------------------------------------------------- #
def describe_table(instance: dict) -> None:
    name = next(
        (t["Value"] for t in instance.get("Tags", []) if t["Key"] == "Name"), "-"
    )
    rows = [
        ("Name", name),
        ("InstanceId", instance["InstanceId"]),
        ("InstanceType", instance["InstanceType"]),
        ("State", instance["State"]["Name"]),
        ("PublicIpAddress", instance.get("PublicIpAddress", "-")),
        ("AvailabilityZone", instance["Placement"]["AvailabilityZone"]),
    ]
    width = max(len(k) for k, _ in rows)
    print()
    for key, value in rows:
        print(f"    {key.ljust(width)}  {value}")


def wait_for_ssh(key_file: Path, ip: str, attempts: int = 12) -> bool:
    for _ in range(attempts):
        result = subprocess.run(
            ["ssh", "-i", str(key_file), "-o", "StrictHostKeyChecking=accept-new",
             "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", f"ubuntu@{ip}", "true"],
            capture_output=True,
        )
        if result.returncode == 0:
            return True
        print(f"    {DIM}.{RESET}", end="", flush=True)
        time.sleep(10)
    print()
    return False


# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="CITS5503 Lab 2 - boto3 EC2 instance")
    parser.add_argument("--prefix", help="resource-name prefix (overrides LAB_PREFIX)")
    parser.add_argument("--status", action="store_true",
                        help="report existing resources and create nothing")
    parser.add_argument("--ssh", action="store_true",
                        help="open an ssh session once the instance is up")
    args = parser.parse_args()

    log("Preflight")

    # Every setting below is read from lab.env; nothing is hardcoded here.
    cfg = load_lab_env(args.prefix)
    prefix = cfg["prefix"]
    region = cfg["region"]
    ami = cfg["ami"]
    instance_type = cfg["instance_type"]
    sg_name = cfg["sg_name"]
    key_name = cfg["key_name"]
    vm_name = cfg["vm_name"]
    key_file = Path(cfg["key_file"])

    session = boto3.session.Session(region_name=region)
    ec2 = session.client("ec2")

    try:
        arn = session.client("sts").get_caller_identity()["Arn"]
    except NoCredentialsError:
        die("No AWS credentials found. Run: aws configure")
    except (ClientError, BotoCoreError) as exc:
        die(
            f"AWS call failed: {exc}\n"
            "  If this mentions an expired signature, your WSL clock drifted:\n"
            "     sudo hwclock -s"
        )

    ok(f"Identity: {arn}")

    if args.status:
        log(f"Current state in {region}")
        sg_id = find_security_group(ec2, sg_name)
        instance = find_instance(ec2, vm_name)
        print(f"    security group : {sg_name} -> {sg_id or 'not created'}")
        print(f"    key pair       : {key_name} -> "
              f"{'exists in AWS' if key_pair_exists(ec2, key_name) else 'not in AWS'}")
        print(f"    pem file       : {key_file} "
              f"{'(mode %o)' % (key_file.stat().st_mode & 0o777) if key_file.exists() else '(missing)'}")
        print(f"    instance       : {vm_name} -> "
              f"{instance['InstanceId'] if instance else 'not created'}")
        if instance:
            describe_table(instance)
        return 0

    try:
        group_id = step1_security_group(ec2, sg_name)
        step2_authorize_ssh(ec2, group_id)
        step3_key_pair(ec2, key_name, key_file)

        existing = find_instance(ec2, vm_name)
        if existing:
            instance_id = existing["InstanceId"]
            log("[4] Create the instance")
            ok(f"Instance tagged {vm_name} already exists: {instance_id} "
               "(not launching another)")
        else:
            instance_id = step4_run_instance(ec2, ami, group_id, key_name, instance_type)

        step5_tag_instance(ec2, instance_id, vm_name)
        public_ip = step6_public_ip(ec2, instance_id)

    except NoCredentialsError:
        die("No AWS credentials found. Run: aws configure")
    except (ClientError, BotoCoreError) as exc:
        die(f"AWS call failed: {exc}")

    instance = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]
    describe_table(instance)

    # Merged back into lab.env so the verify script and later labs can read them.
    for key, value in (
        ("SG_NAME_BOTO", sg_name),
        ("SG_ID_BOTO", group_id),
        ("KEY_NAME_BOTO", key_name),
        ("KEY_FILE_BOTO", str(key_file)),
        ("VM2_NAME", vm_name),
        ("VM2_ID", instance_id),
        ("VM2_IP", public_ip),
    ):
        write_lab_env(key, value)
    ok(f"Saved VM2_ID and VM2_IP into {ENV_FILE}")

    log("[7] Connect via ssh (optional)")
    print(f"    {DIM}$ ssh -i {key_file} ubuntu@{public_ip}{RESET}")
    if args.ssh:
        info("the instance needs ~30-60s after 'running' before sshd answers")
        if wait_for_ssh(key_file, public_ip):
            ok("sshd is up")
        os.execvp(
            "ssh",
            ["ssh", "-i", str(key_file),
             "-o", "StrictHostKeyChecking=accept-new", f"ubuntu@{public_ip}"],
        )

    log("[8] List the instance in the AWS Management Console")
    print(f"""
  Console (scoped to your region):
    https://{region}.console.aws.amazon.com/ec2/home?region={region}#Instances:

  For the live demo of Checkpoint 2:
    - open this file and point at step4_run_instance() and step5_tag_instance()
    - then show {vm_name} in the console, distinct from {prefix}-vm1
    - you do not need to rerun this script during the demo

  Cleanup must be done in the AWS Management Console for Checkpoint 3.
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
