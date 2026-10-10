#!/usr/bin/env python3
"""
CITS5503 Lab 6 - Set up an EC2 instance
---------------------------------------
Creates, in the default VPC of your assigned region:

  * a security group  <prefix>-sg-lab6   (inbound TCP 22 and TCP 80)
  * a key pair        <prefix>-key       (reused if it already exists)
  * one t3.micro instance <prefix>-vm1, tagged with that name

Idempotent: an existing security group, key pair or running instance with the
same name is reused rather than duplicated. Resource IDs are written back to
lab.env under LAB6_* keys so the deploy, ALB and verify scripts find them.

Run with the shared venv active:
    labenv
    python3 lab6_ec2.py
"""

import sys
from pathlib import Path

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    sys.exit("boto3 not found. Activate the shared lab venv first:  labenv")

from lab6_env import load_env, require, update_env, report

# Fixed by the lab specification, not configuration.
INSTANCE_TYPE = "t3.micro"
SSH_USER = "ubuntu"

cfg = load_env()
LAB_PREFIX, AWS_REGION, AWS_AMI = require(cfg, "LAB_PREFIX", "AWS_REGION", "AWS_AMI")

SG_NAME = f"{LAB_PREFIX}-sg-lab6"
VM_NAME = f"{LAB_PREFIX}-vm1"
KEY_NAME = cfg.get("KEY_NAME") or f"{LAB_PREFIX}-key"
KEY_PATH = Path(cfg.get("KEY_PATH") or (Path.home() / ".ssh" / f"{KEY_NAME}.pem")).expanduser()

report({
    "LAB_PREFIX": LAB_PREFIX,
    "AWS_REGION": AWS_REGION,
    "AWS_AMI": AWS_AMI,
    "Instance type": INSTANCE_TYPE,
    "Security group": SG_NAME,
    "Key pair": f"{KEY_NAME}  ({KEY_PATH})",
    "Instance name": VM_NAME,
})

ec2 = boto3.client("ec2", region_name=AWS_REGION)


def default_vpc_id() -> str:
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        sys.exit(f"No default VPC in {AWS_REGION}. Create one, or pass a VpcId explicitly.")
    return vpcs[0]["VpcId"]


def ensure_security_group(vpc_id: str) -> str:
    """Create <prefix>-sg-lab6 with SSH and HTTP open, or reuse the existing one."""
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
            Description="CITS5503 Lab 6 - SSH and HTTP for the Django/nginx instance",
            VpcId=vpc_id,
        )["GroupId"]
        print(f"Created security group {SG_NAME}: {sg_id}")

    # Port 22 for the Fabric/SSH deploy, port 80 for nginx and the ALB health check.
    for port, label in ((22, "SSH"), (80, "HTTP")):
        try:
            ec2.authorize_security_group_ingress(
                GroupId=sg_id,
                IpPermissions=[{
                    "IpProtocol": "tcp",
                    "FromPort": port,
                    "ToPort": port,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": f"Lab 6 {label}"}],
                }],
            )
            print(f"  authorised inbound tcp/{port} ({label})")
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "InvalidPermission.Duplicate":
                print(f"  inbound tcp/{port} ({label}) already authorised")
            else:
                raise
    return sg_id


def ensure_key_pair() -> None:
    """Reuse the existing key pair when the private key is still on disk."""
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)

    try:
        ec2.describe_key_pairs(KeyNames=[KEY_NAME])
        in_aws = True
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "InvalidKeyPair.NotFound":
            raise
        in_aws = False

    if in_aws and KEY_PATH.exists():
        print(f"Key pair {KEY_NAME} exists and {KEY_PATH} is present - reusing")
        KEY_PATH.chmod(0o400)
        return

    if in_aws and not KEY_PATH.exists():
        # AWS only hands out the private key once, so a key with no local .pem
        # is unusable. Replace it.
        print(f"Key pair {KEY_NAME} exists in AWS but {KEY_PATH} is missing - recreating")
        ec2.delete_key_pair(KeyName=KEY_NAME)

    material = ec2.create_key_pair(KeyName=KEY_NAME)["KeyMaterial"]
    KEY_PATH.write_text(material)
    KEY_PATH.chmod(0o400)
    print(f"Created key pair {KEY_NAME}, private key written to {KEY_PATH}")


def find_instance() -> dict | None:
    reservations = ec2.describe_instances(
        Filters=[{"Name": "tag:Name", "Values": [VM_NAME]},
                 {"Name": "instance-state-name", "Values": ["pending", "running"]}]
    )["Reservations"]
    for res in reservations:
        for inst in res["Instances"]:
            return inst
    return None


def launch_instance(sg_id: str) -> dict:
    inst = find_instance()
    if inst:
        print(f"Instance {VM_NAME} already exists: {inst['InstanceId']} ({inst['State']['Name']})")
    else:
        inst = ec2.run_instances(
            ImageId=AWS_AMI,
            InstanceType=INSTANCE_TYPE,
            MinCount=1,
            MaxCount=1,
            KeyName=KEY_NAME,
            SecurityGroupIds=[sg_id],          # the ID, not the group name
            TagSpecifications=[{
                "ResourceType": "instance",
                "Tags": [{"Key": "Name", "Value": VM_NAME}],
            }],
        )["Instances"][0]
        print(f"Launched {VM_NAME}: {inst['InstanceId']}")

    instance_id = inst["InstanceId"]
    print("Waiting for the instance to reach 'running' ...")
    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])

    inst = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]
    return inst


def main() -> None:
    vpc_id = default_vpc_id()
    print(f"Default VPC: {vpc_id}\n")

    sg_id = ensure_security_group(vpc_id)
    ensure_key_pair()
    inst = launch_instance(sg_id)

    public_ip = inst.get("PublicIpAddress", "")
    public_dns = inst.get("PublicDnsName", "")
    az = inst["Placement"]["AvailabilityZone"]

    update_env({
        "KEY_NAME": KEY_NAME,
        "KEY_PATH": str(KEY_PATH),
        "SSH_USER": SSH_USER,
        "LAB6_SG_ID": sg_id,
        "LAB6_SG_NAME": SG_NAME,
        "LAB6_VPC_ID": inst["VpcId"],
        "LAB6_SUBNET_ID": inst["SubnetId"],
        "LAB6_VM1_NAME": VM_NAME,
        "LAB6_VM1_ID": inst["InstanceId"],
        "LAB6_VM1_IP": public_ip,
        "LAB6_VM1_DNS": public_dns,
        "LAB6_VM1_AZ": az,
    })

    print()
    print("=" * 62)
    print(f"  {VM_NAME}")
    print(f"  Instance ID   {inst['InstanceId']}")
    print(f"  Type          {inst['InstanceType']}")
    print(f"  AZ / subnet   {az} / {inst['SubnetId']}")
    print(f"  Public IP     {public_ip or 'not assigned yet'}")
    print(f"  Public DNS    {public_dns or '-'}")
    print("=" * 62)
    print(f"\n  ssh -i {KEY_PATH} {SSH_USER}@{public_ip}")
    print("\nNext:  python3 lab6_ddb_sync.py   then   ./lab6_deploy.sh")


if __name__ == "__main__":
    main()
