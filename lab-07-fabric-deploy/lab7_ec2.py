#!/usr/bin/env python3
"""
CITS5503 Lab 7 - create the EC2 instance Fabric will deploy to
--------------------------------------------------------------
Creates, in the default VPC of your assigned region:

  * a security group  <prefix>-sg-lab7   (inbound TCP 22 and TCP 80)
  * a key pair        <prefix>-key       (reused if Lab 5/6 already made one)
  * one t3.micro instance <prefix>-vm1, tagged with that name

Port 22 is what Fabric connects over; port 80 is what the browser hits for
Checkpoint 2. Idempotent throughout: an existing group, key pair or running
instance of the same name is reused rather than duplicated.

Resource IDs are written back to lab.env under LAB7_* keys. KEY_NAME, KEY_PATH,
SSH_USER and DJANGO_PORT are only written if an earlier lab has not already
recorded them, so Lab 7 never ends up with a second copy of a value that is
already there.

Run with the shared venv active:
    labenv
    python3 lab7_ec2.py

    python3 lab7_ec2.py --cleanup     # terminate for a repeat practice run
"""

import argparse
import sys
import time
from pathlib import Path

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    sys.exit("boto3 not found. Activate the shared lab venv first:  labenv")

from lab7_env import load_env, require, optional, update_env, set_if_absent, report

# Fixed by the lab specification, not configuration.
INSTANCE_TYPE = "t3.micro"
SSH_PORT = 22
HTTP_PORT = 80
OPEN_CIDR = "0.0.0.0/0"

cfg = load_env()
LAB_PREFIX, AWS_REGION, AWS_AMI = require(cfg, "LAB_PREFIX", "AWS_REGION", "AWS_AMI")

SG_NAME = f"{LAB_PREFIX}-sg-lab7"
VM_NAME = f"{LAB_PREFIX}-vm1"
KEY_NAME = optional(cfg, "KEY_NAME", f"{LAB_PREFIX}-key")
KEY_PATH = Path(optional(cfg, "KEY_PATH", str(Path.home() / ".ssh" / f"{KEY_NAME}.pem"))).expanduser()
SSH_USER = optional(cfg, "SSH_USER", "ubuntu")
DJANGO_PORT = optional(cfg, "DJANGO_PORT", "8000")

ec2 = boto3.client("ec2", region_name=AWS_REGION)


# --------------------------------------------------------------------- VPC
def default_vpc_id() -> str:
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        sys.exit(f"No default VPC in {AWS_REGION}. Create one, or pass a VpcId explicitly.")
    return vpcs[0]["VpcId"]


def pick_subnet(vpc_id: str) -> tuple:
    """A subnet in a real Availability Zone that offers t3.micro.

    zone-type is filtered explicitly: ap-southeast-2 exposes the Auckland Local
    Zone (ap-southeast-2-akl-1a), which does not offer t3.micro and fails at
    RunInstances with VolumeTypeNotAvailableInZone.
    """
    zones = [
        z["ZoneName"]
        for z in ec2.describe_availability_zones(
            Filters=[
                {"Name": "zone-type", "Values": ["availability-zone"]},
                {"Name": "state", "Values": ["available"]},
            ]
        )["AvailabilityZones"]
    ]

    offered = {
        o["Location"]
        for o in ec2.describe_instance_type_offerings(
            LocationType="availability-zone",
            Filters=[{"Name": "instance-type", "Values": [INSTANCE_TYPE]}],
        )["InstanceTypeOfferings"]
    }
    usable = [z for z in zones if z in offered]
    if not usable:
        sys.exit(f"No availability zone in {AWS_REGION} offers {INSTANCE_TYPE}.")

    subnets = ec2.describe_subnets(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "availability-zone", "Values": usable},
        ]
    )["Subnets"]
    if not subnets:
        sys.exit(f"No subnet in {vpc_id} sits in one of {usable}.")

    # Prefer a subnet that auto-assigns a public IPv4, so the browser can reach it.
    subnets.sort(key=lambda s: (not s.get("MapPublicIpOnLaunch", False), s["AvailabilityZone"]))
    chosen = subnets[0]
    return chosen["SubnetId"], chosen["AvailabilityZone"]


# ---------------------------------------------------------- security group
def ensure_security_group(vpc_id: str) -> str:
    existing = ec2.describe_security_groups(
        Filters=[{"Name": "group-name", "Values": [SG_NAME]},
                 {"Name": "vpc-id", "Values": [vpc_id]}]
    )["SecurityGroups"]

    if existing:
        sg_id = existing[0]["GroupId"]
        print(f"Security group {SG_NAME} already exists: {sg_id}")
    else:
        sg_id = ec2.create_security_group(
            GroupName=SG_NAME,
            Description="CITS5503 Lab 7 - SSH for Fabric, HTTP for nginx",
            VpcId=vpc_id,
        )["GroupId"]
        print(f"Created security group {SG_NAME}: {sg_id}")

    for port, why in ((SSH_PORT, "Fabric/SSH"), (HTTP_PORT, "nginx")):
        try:
            ec2.authorize_security_group_ingress(
                GroupId=sg_id,
                IpPermissions=[{
                    "IpProtocol": "tcp",
                    "FromPort": port,
                    "ToPort": port,
                    "IpRanges": [{"CidrIp": OPEN_CIDR, "Description": f"CITS5503 Lab 7 {why}"}],
                }],
            )
            print(f"  opened TCP {port} to {OPEN_CIDR} ({why})")
        except ClientError as e:
            if e.response["Error"]["Code"] == "InvalidPermission.Duplicate":
                print(f"  TCP {port} already open")
            else:
                raise
    return sg_id


# ---------------------------------------------------------------- key pair
def ensure_key_pair() -> None:
    try:
        ec2.describe_key_pairs(KeyNames=[KEY_NAME])
        exists_in_aws = True
    except ClientError as e:
        if e.response["Error"]["Code"] != "InvalidKeyPair.NotFound":
            raise
        exists_in_aws = False

    if exists_in_aws and KEY_PATH.exists():
        print(f"Key pair {KEY_NAME} already exists, private key at {KEY_PATH}")
        return

    if exists_in_aws and not KEY_PATH.exists():
        sys.exit(
            f"Key pair {KEY_NAME} exists in AWS but {KEY_PATH} is missing.\n"
            "AWS never returns a private key twice. Delete the key pair in the\n"
            "console (EC2 -> Key Pairs), terminate anything still using it, then\n"
            "re-run this script to create a fresh one."
        )

    material = ec2.create_key_pair(KeyName=KEY_NAME, KeyType="rsa")["KeyMaterial"]
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEY_PATH.write_text(material)
    KEY_PATH.chmod(0o600)
    print(f"Created key pair {KEY_NAME}, private key written to {KEY_PATH} (mode 600)")

    if str(KEY_PATH).startswith("/mnt/"):
        print("  WARNING: that path is on the Windows drive. chmod is a no-op on")
        print("  DrvFs and ssh will refuse the key. Move it under ~/.ssh.")


# ---------------------------------------------------------------- instance
def find_instance() -> dict:
    reservations = ec2.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [VM_NAME]},
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
        ]
    )["Reservations"]
    for r in reservations:
        for inst in r["Instances"]:
            return inst
    return {}


def ensure_instance(sg_id: str, subnet_id: str) -> dict:
    inst = find_instance()

    if inst:
        iid = inst["InstanceId"]
        state = inst["State"]["Name"]
        print(f"Instance {VM_NAME} already exists: {iid} ({state})")
        if state in ("stopped", "stopping"):
            if state == "stopping":
                print("  waiting for it to finish stopping...")
                ec2.get_waiter("instance_stopped").wait(InstanceIds=[iid])
            print("  starting it...")
            ec2.start_instances(InstanceIds=[iid])
            print("  NOTE: a restarted instance gets a new public IP. Re-run")
            print("  lab7_sshconfig.py afterwards so ~/.ssh/config is not stale.")
        sgs = ", ".join(g["GroupName"] for g in inst.get("SecurityGroups", []))
        print(f"  security groups: {sgs or 'none'}")
        if inst.get("InstanceType") != INSTANCE_TYPE:
            print(f"  WARNING: type is {inst.get('InstanceType')}, the lab expects {INSTANCE_TYPE}")
    else:
        print(f"Launching {VM_NAME} ({INSTANCE_TYPE}, {AWS_AMI}) in {subnet_id}...")
        inst = ec2.run_instances(
            ImageId=AWS_AMI,
            InstanceType=INSTANCE_TYPE,
            KeyName=KEY_NAME,
            MinCount=1,
            MaxCount=1,
            NetworkInterfaces=[{
                "DeviceIndex": 0,
                "SubnetId": subnet_id,
                "Groups": [sg_id],
                "AssociatePublicIpAddress": True,
            }],
            TagSpecifications=[{
                "ResourceType": "instance",
                "Tags": [{"Key": "Name", "Value": VM_NAME}],
            }],
        )["Instances"][0]
        print(f"  {inst['InstanceId']} launched")

    iid = inst["InstanceId"]
    print("  waiting for it to reach 'running'...")
    ec2.get_waiter("instance_running").wait(InstanceIds=[iid])

    # Re-describe: the public address only appears once the instance is running.
    for _ in range(30):
        inst = ec2.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]
        if inst.get("PublicIpAddress"):
            break
        time.sleep(2)

    if not inst.get("PublicIpAddress"):
        sys.exit(
            f"{iid} is running but has no public IPv4 address.\n"
            "Checkpoint 2 needs one. Check the subnet's auto-assign setting."
        )
    return inst


# ----------------------------------------------------------------- cleanup
def cleanup() -> None:
    """Terminate the instance so the lab can be rehearsed from scratch.

    Checkpoint 3 itself must be demonstrated in the AWS Management Console.
    This exists so repeat practice runs do not leave instances behind.
    """
    inst = find_instance()
    if not inst:
        print(f"No instance named {VM_NAME} to terminate.")
    else:
        iid = inst["InstanceId"]
        print(f"Terminating {VM_NAME} ({iid})...")
        ec2.terminate_instances(InstanceIds=[iid])
        ec2.get_waiter("instance_terminated").wait(InstanceIds=[iid])
        print("  terminated")

    addrs = ec2.describe_addresses()["Addresses"]
    unassociated = [a for a in addrs if not a.get("AssociationId")]
    for a in unassociated:
        print(f"Releasing unassociated Elastic IP {a.get('PublicIp')}")
        ec2.release_address(AllocationId=a["AllocationId"])
    if not unassociated:
        print("No unassociated Elastic IPs to release.")

    update_env({"LAB7_VM_ID": "", "LAB7_VM_IP": "", "LAB7_VM_DNS": "", "LAB7_VM_AZ": ""})
    print("\nCleared the LAB7 address keys in lab.env.")
    print("The security group and key pair were left in place for the next run.")


# --------------------------------------------------------------------- run
def main() -> None:
    ap = argparse.ArgumentParser(description="Create the Lab 7 EC2 instance.")
    ap.add_argument("--cleanup", action="store_true",
                    help="terminate the instance and release unassociated Elastic IPs")
    args = ap.parse_args()

    report({
        "LAB_PREFIX": LAB_PREFIX,
        "AWS_REGION": AWS_REGION,
        "AWS_AMI": AWS_AMI,
        "Instance type": INSTANCE_TYPE,
        "Security group": SG_NAME,
        "Key pair": f"{KEY_NAME}  ({KEY_PATH})",
        "Instance name": VM_NAME,
        "SSH user": SSH_USER,
        "Django port": DJANGO_PORT,
    })

    if args.cleanup:
        cleanup()
        return

    vpc_id = default_vpc_id()
    subnet_id, az = pick_subnet(vpc_id)
    print(f"Default VPC {vpc_id}, subnet {subnet_id} in {az}\n")

    sg_id = ensure_security_group(vpc_id)
    ensure_key_pair()
    inst = ensure_instance(sg_id, subnet_id)

    update_env({
        "LAB7_SG_NAME": SG_NAME,
        "LAB7_SG_ID": sg_id,
        "LAB7_VPC_ID": vpc_id,
        "LAB7_SUBNET_ID": inst.get("SubnetId", subnet_id),
        "LAB7_VM_NAME": VM_NAME,
        "LAB7_VM_ID": inst["InstanceId"],
        "LAB7_VM_IP": inst["PublicIpAddress"],
        "LAB7_VM_DNS": inst.get("PublicDnsName", ""),
        "LAB7_VM_AZ": inst["Placement"]["AvailabilityZone"],
        "LAB7_INSTANCE_TYPE": INSTANCE_TYPE,
    })

    # Only recorded if Labs 1-6 did not already record them.
    written = set_if_absent(cfg, {
        "KEY_NAME": KEY_NAME,
        "KEY_PATH": str(KEY_PATH),
        "SSH_USER": SSH_USER,
        "DJANGO_PORT": DJANGO_PORT,
    })

    print()
    print(f"  instance   {inst['InstanceId']}  ({INSTANCE_TYPE}, {inst['Placement']['AvailabilityZone']})")
    print(f"  public IP  {inst['PublicIpAddress']}")
    print(f"  public DNS {inst.get('PublicDnsName', '(none)')}")
    if written:
        print(f"\n  added to lab.env: {', '.join(sorted(written))}")
    print("\nNext:  python3 lab7_sshconfig.py")


if __name__ == "__main__":
    main()
