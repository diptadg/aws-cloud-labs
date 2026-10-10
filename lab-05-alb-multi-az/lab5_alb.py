#!/usr/bin/env python3
"""
CITS5503 Lab 5 - Networking
---------------------------
Creates, in the default VPC:

  * a security group  <prefix>-sg-lab5   (inbound TCP 22 + TCP 80)
  * a key pair        <prefix>-key        (reused if it already exists)
  * two t3.micro EC2 instances <prefix>-vm1 and <prefix>-vm2, each in a
    DIFFERENT availability zone (Checkpoint 1 requires two distinct AZs)
  * an internet-facing Application Load Balancer <prefix>-alb spanning the
    two subnets used by the instances
  * a target group <prefix>-tg (HTTP:80, target type "instance")
  * the two instances registered as targets
  * an HTTP:80 listener whose default action forwards to the target group

The script is idempotent: re-running it reuses anything that already exists
instead of creating duplicates.

Run it with the lab venv active:
    labenv
    python3 lab5_alb.py

All configuration comes from ~/cits5503/lab.env - nothing is hard-coded and
there are no fallback defaults. The file must define:

    LAB_PREFIX       e.g. myname
    AWS_REGION       e.g. ap-southeast-2
    AWS_AMI          e.g. ami-xxxxxxxxxxxxxxxxx

Resource IDs are written back to the same file under LAB5_* keys.
"""

import os
import sys
from pathlib import Path

try:
    import boto3
    from botocore.exceptions import ClientError, WaiterError
except ImportError:
    sys.exit("boto3 not found. Activate the lab venv first:  labenv")

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

# Location of the config file. Override with:  LAB_ENV=/path/to/file python3 lab5_alb.py
ENV_FILE = Path(os.environ.get("LAB_ENV", Path.home() / "cits5503" / "lab.env"))

# Every setting comes from lab.env. No defaults, no fallbacks.
REQUIRED_KEYS = ("LAB_PREFIX", "AWS_REGION", "AWS_AMI")

# Fixed by the lab sheet, not by configuration.
INSTANCE_TYPE = "t3.micro"          # the lab mandates this type
LISTENER_PORT = 80                  # "Protocol: HTTP and Port 80"
SSH_PORT = 22
SSH_USER = "ubuntu"                 # login user for the Ubuntu AMI
INGRESS_CIDR = "0.0.0.0/0"

# Resource-name suffixes. These are appended to LAB_PREFIX and then written
# into lab.env, so the shell scripts read the names rather than rebuilding them.
SUFFIX_SG = "-sg-lab5"
SUFFIX_KEY = "-key"
SUFFIX_VM1 = "-vm1"                 # naming format required by the checkpoints
SUFFIX_VM2 = "-vm2"
SUFFIX_TG = "-tg"
SUFFIX_ALB = "-alb"


def load_env() -> dict:
    """Read simple KEY=VALUE lines out of ~/cits5503/lab.env and validate them."""
    if not ENV_FILE.exists():
        sys.exit(f"Config file not found: {ENV_FILE}\n"
                 f"It must define: {', '.join(REQUIRED_KEYS)}")

    cfg = {}
    for raw in ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        cfg[key.strip()] = val.strip().strip('"').strip("'")

    missing = [k for k in REQUIRED_KEYS if not cfg.get(k)]
    if missing:
        sys.exit(f"{ENV_FILE} is missing or has empty values for: {', '.join(missing)}")
    return cfg


def update_env(updates: dict) -> None:
    """Insert/replace KEY="value" lines in lab.env so the shell scripts can read them."""
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    for key, val in updates.items():
        new_line = f'{key}="{val}"'
        for i, existing in enumerate(lines):
            stripped = existing.strip()
            if stripped.startswith(f"{key}=") or stripped.startswith(f"export {key}="):
                lines[i] = new_line
                break
        else:
            lines.append(new_line)
    ENV_FILE.write_text("\n".join(lines) + "\n")


def log(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def err_code(exc: ClientError) -> str:
    return exc.response.get("Error", {}).get("Code", "")


# --------------------------------------------------------------------------
# Step 1 - network discovery (default VPC + two subnets in two AZs)
# --------------------------------------------------------------------------

def find_default_vpc(ec2) -> str:
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        sys.exit("No default VPC found in this region - create one or supply a VPC id.")
    vpc_id = vpcs[0]["VpcId"]
    log(f"Default VPC: {vpc_id}")
    return vpc_id


def pick_two_subnets(ec2, vpc_id: str):
    """Return two subnets that live in two different availability zones.

    An Application Load Balancer must span at least two AZs, and Checkpoint 1
    asks for the two instances to be in different AZs, so the same pair of
    subnets is used for both.
    """
    subnets = ec2.describe_subnets(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "default-for-az", "Values": ["true"]},
        ]
    )["Subnets"]
    if len(subnets) < 2:
        # fall back to every subnet in the VPC if the default-for-az filter is too narrow
        subnets = ec2.describe_subnets(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        )["Subnets"]

    by_az = {}
    for sn in sorted(subnets, key=lambda s: s["AvailabilityZone"]):
        by_az.setdefault(sn["AvailabilityZone"], sn)

    if len(by_az) < 2:
        sys.exit("Need subnets in at least two availability zones for an ALB.")

    azs = sorted(by_az)[:2]
    chosen = [by_az[az] for az in azs]
    for sn in chosen:
        log(f"Subnet {sn['SubnetId']}  AZ {sn['AvailabilityZone']}")
    return chosen


# --------------------------------------------------------------------------
# Step 2 - security group (SSH + HTTP)
# --------------------------------------------------------------------------

def ensure_security_group(ec2, name: str, vpc_id: str) -> str:
    try:
        groups = ec2.describe_security_groups(
            Filters=[
                {"Name": "group-name", "Values": [name]},
                {"Name": "vpc-id", "Values": [vpc_id]},
            ]
        )["SecurityGroups"]
    except ClientError:
        groups = []

    if groups:
        sg_id = groups[0]["GroupId"]
        log(f"Reusing security group {name} ({sg_id})")
    else:
        sg_id = ec2.create_security_group(
            GroupName=name,
            Description="CITS5503 Lab 5 - SSH and HTTP for EC2 + ALB",
            VpcId=vpc_id,
        )["GroupId"]
        log(f"Created security group {name} ({sg_id})")

    # Authorise inbound SSH (22) and HTTP (80). Duplicate rules are harmless.
    for port, label in ((SSH_PORT, "SSH"), (LISTENER_PORT, "HTTP")):
        try:
            ec2.authorize_security_group_ingress(
                GroupId=sg_id,   # NOTE: always the sg-xxxx ID, never the group name
                IpPermissions=[
                    {
                        "IpProtocol": "tcp",
                        "FromPort": port,
                        "ToPort": port,
                        "IpRanges": [{"CidrIp": INGRESS_CIDR,
                                      "Description": f"lab5 {label}"}],
                    }
                ],
            )
            log(f"Authorised inbound {label} (tcp/{port})")
        except ClientError as exc:
            if err_code(exc) == "InvalidPermission.Duplicate":
                log(f"Inbound {label} (tcp/{port}) already authorised")
            else:
                raise
    return sg_id


# --------------------------------------------------------------------------
# Step 3 - key pair
# --------------------------------------------------------------------------

def ensure_key_pair(ec2, key_name: str) -> Path:
    """Create the key pair if absent and store the .pem under ~/.ssh.

    The .pem deliberately lives on the Linux filesystem: a key kept on /mnt/c
    inherits DrvFs permissions that OpenSSH rejects as "too open".
    """
    pem_path = Path.home() / ".ssh" / f"{key_name}.pem"
    pem_path.parent.mkdir(mode=0o700, exist_ok=True)

    exists = True
    try:
        ec2.describe_key_pairs(KeyNames=[key_name])
    except ClientError as exc:
        if "NotFound" in err_code(exc):
            exists = False
        else:
            raise

    if exists:
        if pem_path.exists():
            log(f"Reusing key pair {key_name} ({pem_path})")
        else:
            log(f"WARNING: key pair {key_name} exists in AWS but {pem_path} is missing.")
            log("         AWS will not re-issue the private key. To get a usable key, run:")
            log(f"           aws ec2 delete-key-pair --key-name {key_name}")
            log("         then re-run this script (only safe if no instance still needs it).")
        return pem_path

    material = ec2.create_key_pair(
        KeyName=key_name, KeyType="rsa", KeyFormat="pem"
    )["KeyMaterial"]
    pem_path.write_text(material)
    pem_path.chmod(0o400)
    log(f"Created key pair {key_name} and saved private key to {pem_path}")
    return pem_path


# --------------------------------------------------------------------------
# Step 4 - EC2 instances (one per AZ)
# --------------------------------------------------------------------------

def find_instance_by_name(ec2, name: str):
    reservations = ec2.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [name]},
            {"Name": "instance-state-name",
             "Values": ["pending", "running", "stopping", "stopped"]},
        ]
    )["Reservations"]
    for res in reservations:
        for inst in res["Instances"]:
            return inst
    return None


def launch_instance(ec2, cfg, name, subnet_id, sg_id, key_name) -> str:
    existing = find_instance_by_name(ec2, name)
    if existing:
        log(f"Reusing instance {name} ({existing['InstanceId']}, "
            f"AZ {existing['Placement']['AvailabilityZone']}, "
            f"state {existing['State']['Name']})")
        if existing["State"]["Name"] in ("stopped", "stopping"):
            log(f"Starting {name} again ...")
            ec2.start_instances(InstanceIds=[existing["InstanceId"]])
        return existing["InstanceId"]

    resp = ec2.run_instances(
        ImageId=cfg["AWS_AMI"],
        InstanceType=INSTANCE_TYPE,            # t3.micro - required by the lab
        KeyName=key_name,
        MinCount=1,
        MaxCount=1,
        NetworkInterfaces=[
            {
                "DeviceIndex": 0,
                "SubnetId": subnet_id,          # this is what pins the AZ
                "Groups": [sg_id],
                "AssociatePublicIpAddress": True,
                "DeleteOnTermination": True,
            }
        ],
        # Tagging at launch means the Name tag is set the moment the instance
        # appears in the console - no follow-up create-tags call needed.
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [{"Key": "Name", "Value": name}],
            }
        ],
    )
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"Launched {name} ({instance_id}) in subnet {subnet_id}")
    return instance_id


def wait_running(ec2, instance_ids):
    log("Waiting for instances to reach 'running' ...")
    ec2.get_waiter("instance_running").wait(InstanceIds=instance_ids)
    log("Instances are running")


def describe_instances(ec2, instance_ids):
    out = []
    for res in ec2.describe_instances(InstanceIds=instance_ids)["Reservations"]:
        out.extend(res["Instances"])
    return {i["InstanceId"]: i for i in out}


# --------------------------------------------------------------------------
# Step 5 - target group, load balancer, listener
# --------------------------------------------------------------------------

def ensure_target_group(elbv2, name: str, vpc_id: str) -> str:
    try:
        tgs = elbv2.describe_target_groups(Names=[name])["TargetGroups"]
        log(f"Reusing target group {name}")
        return tgs[0]["TargetGroupArn"]
    except ClientError as exc:
        if err_code(exc) not in ("TargetGroupNotFound", "ValidationError"):
            raise

    tg = elbv2.create_target_group(
        Name=name,
        Protocol="HTTP",
        Port=LISTENER_PORT,
        VpcId=vpc_id,                      # same VPC the instances live in
        TargetType="instance",
        HealthCheckProtocol="HTTP",
        HealthCheckPath="/",
        HealthCheckIntervalSeconds=15,
        HealthCheckTimeoutSeconds=5,
        HealthyThresholdCount=2,
        UnhealthyThresholdCount=2,
        Matcher={"HttpCode": "200"},
    )["TargetGroups"][0]
    log(f"Created target group {name}")
    return tg["TargetGroupArn"]


def ensure_load_balancer(elbv2, name: str, subnet_ids, sg_id: str):
    try:
        lbs = elbv2.describe_load_balancers(Names=[name])["LoadBalancers"]
        log(f"Reusing load balancer {name}")
        return lbs[0]
    except ClientError as exc:
        if err_code(exc) not in ("LoadBalancerNotFound", "ValidationError"):
            raise

    lb = elbv2.create_load_balancer(
        Name=name,
        Subnets=subnet_ids,                # two subnets => two AZs
        SecurityGroups=[sg_id],
        Scheme="internet-facing",
        Type="application",
        IpAddressType="ipv4",
        Tags=[{"Key": "Name", "Value": name}],
    )["LoadBalancers"][0]
    log(f"Created load balancer {name}")
    return lb


def ensure_listener(elbv2, lb_arn: str, tg_arn: str) -> str:
    listeners = elbv2.describe_listeners(LoadBalancerArn=lb_arn)["Listeners"]
    for lst in listeners:
        if lst["Port"] == LISTENER_PORT and lst["Protocol"] == "HTTP":
            log(f"Reusing HTTP:{LISTENER_PORT} listener")
            return lst["ListenerArn"]

    lst = elbv2.create_listener(
        LoadBalancerArn=lb_arn,
        Protocol="HTTP",
        Port=LISTENER_PORT,
        DefaultActions=[{"Type": "forward", "TargetGroupArn": tg_arn}],
    )["Listeners"][0]
    log(f"Created HTTP:{LISTENER_PORT} listener forwarding to the target group")
    return lst["ListenerArn"]


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> None:
    cfg = load_env()
    prefix = cfg["LAB_PREFIX"]
    region = cfg["AWS_REGION"]

    sg_name = f"{prefix}{SUFFIX_SG}"
    key_name = f"{prefix}{SUFFIX_KEY}"
    vm_names = [f"{prefix}{SUFFIX_VM1}", f"{prefix}{SUFFIX_VM2}"]
    tg_name = f"{prefix}{SUFFIX_TG}"
    lb_name = f"{prefix}{SUFFIX_ALB}"

    log(f"Config from {ENV_FILE}")
    log(f"Region {region} | AMI {cfg['AWS_AMI']} | type {INSTANCE_TYPE}")

    ec2 = boto3.client("ec2", region_name=region)
    elbv2 = boto3.client("elbv2", region_name=region)

    vpc_id = find_default_vpc(ec2)
    subnets = pick_two_subnets(ec2, vpc_id)
    subnet_ids = [s["SubnetId"] for s in subnets]

    sg_id = ensure_security_group(ec2, sg_name, vpc_id)
    pem_path = ensure_key_pair(ec2, key_name)

    instance_ids = [
        launch_instance(ec2, cfg, vm_names[i], subnet_ids[i], sg_id, key_name)
        for i in range(2)
    ]
    wait_running(ec2, instance_ids)
    details = describe_instances(ec2, instance_ids)

    tg_arn = ensure_target_group(elbv2, tg_name, vpc_id)
    lb = ensure_load_balancer(elbv2, lb_name, subnet_ids, sg_id)
    lb_arn = lb["LoadBalancerArn"]

    log("Waiting for the load balancer to become active (this takes a couple of minutes) ...")
    try:
        elbv2.get_waiter("load_balancer_available").wait(LoadBalancerArns=[lb_arn])
    except WaiterError:
        log("Waiter timed out - check the ALB state in the console before demoing.")

    elbv2.register_targets(
        TargetGroupArn=tg_arn,
        Targets=[{"Id": iid, "Port": LISTENER_PORT} for iid in instance_ids],
    )
    log("Registered both instances as targets")

    ensure_listener(elbv2, lb_arn, tg_arn)

    lb = elbv2.describe_load_balancers(LoadBalancerArns=[lb_arn])["LoadBalancers"][0]
    lb_dns = lb["DNSName"]

    update_env({
        # names - so the shell scripts read them instead of rebuilding them
        "LAB5_SG_NAME": sg_name,
        "LAB5_VM1_NAME": vm_names[0],
        "LAB5_VM2_NAME": vm_names[1],
        "LAB5_TG_NAME": tg_name,
        "LAB5_ALB_NAME": lb_name,
        "LAB5_INSTANCE_TYPE": INSTANCE_TYPE,
        # ids and endpoints
        "LAB5_SG_ID": sg_id,
        "LAB5_VPC_ID": vpc_id,
        "LAB5_SUBNET_1": subnet_ids[0],
        "LAB5_SUBNET_2": subnet_ids[1],
        "LAB5_VM1_ID": instance_ids[0],
        "LAB5_VM2_ID": instance_ids[1],
        "LAB5_TG_ARN": tg_arn,
        "LAB5_ALB_ARN": lb_arn,
        "LAB5_ALB_DNS": lb_dns,
        # ssh
        "KEY_NAME": key_name,
        "KEY_PATH": str(pem_path),
        "SSH_USER": SSH_USER,
    })

    print()
    print("=" * 68)
    for iid in instance_ids:
        inst = details[iid]
        name = next((t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"), "?")
        print(f"{name:20} {iid}  {inst['Placement']['AvailabilityZone']:16} "
              f"{inst.get('PublicIpAddress', 'pending')}")
    print(f"{'ALB DNS':20} {lb_dns}")
    print(f"{'ALB state':20} {lb['State']['Code']}  ({lb['Scheme']}, "
          f"{len(lb['AvailabilityZones'])} AZs)")
    print("=" * 68)
    print()
    print("Targets will report 'unhealthy' until Apache2 is installed - that is expected.")
    print("Next:  ./lab5_web.sh     (installs Apache2 and sets the page title on each instance)")


if __name__ == "__main__":
    try:
        main()
    except ClientError as exc:
        code = err_code(exc)
        print(f"\nAWS error [{code}]: {exc}", file=sys.stderr)
        if "Signature" in code or "RequestExpired" in code:
            print("Clock drift after a Windows sleep? Run:  sudo hwclock -s", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
