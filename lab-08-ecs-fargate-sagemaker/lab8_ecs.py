#!/usr/bin/env python3
"""CITS5503 Lab 8 - deploy the ECR image onto ECS Fargate.

In order:

  1. security group  - inbound TCP on the Jupyter port, outbound HTTPS 443
  2. subnets         - three subnets of the default VPC, real AZs only
  3. task definition - Fargate, awsvpc, task role SageMakerRole, execution role
                       ecsTaskExecutionRole, environment variables injected
  4. cluster         - created if absent
  5. service         - desiredCount 1, FARGATE, public IP enabled
  6. waits for the service to become stable, then reports the task's public IP

Every step is idempotent: re-running updates the existing service rather than
failing on a duplicate name.

Run with the shared venv active:
    labenv
    python3 lab8_ecs.py

    python3 lab8_ecs.py --ip-only     just re-read the public IP
    python3 lab8_ecs.py --no-logs     skip the CloudWatch log group
    python3 lab8_ecs.py --cleanup     delete the service and the cluster
"""

import argparse
import sys
import time

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    sys.exit("boto3 not found. Activate the shared lab venv first:  labenv")

from lab8_env import load_env, require, optional, update_env, set_if_absent, report

# Fixed by the lab specification, not configuration.
LAUNCH_TYPE = "FARGATE"
NETWORK_MODE = "awsvpc"
DESIRED_COUNT = 1
HTTPS_PORT = 443
OPEN_CIDR = "0.0.0.0/0"
MAX_SUBNETS = 3

cfg = load_env()
LAB_PREFIX, AWS_REGION = require(cfg, "LAB_PREFIX", "AWS_REGION")
IMAGE_URI = require(cfg, "LAB8_IMAGE_URI")
CLUSTER, SERVICE, FAMILY, CONTAINER, SG_NAME = require(
    cfg, "LAB8_CLUSTER", "LAB8_SERVICE_NAME", "LAB8_TASK_FAMILY",
    "LAB8_CONTAINER", "LAB8_SG_NAME",
)

JUPYTER_PORT = int(optional(cfg, "LAB8_JUPYTER_PORT", "8888"))
JUPYTER_TOKEN = require(cfg, "LAB8_JUPYTER_TOKEN")
TASK_ROLE = optional(cfg, "LAB8_TASK_ROLE", "SageMakerRole")
EXEC_ROLE = optional(cfg, "LAB8_EXEC_ROLE", "ecsTaskExecutionRole")
TASK_CPU = optional(cfg, "LAB8_TASK_CPU", "256")
TASK_MEMORY = optional(cfg, "LAB8_TASK_MEMORY", "512")
IMAGE_ARCH = optional(cfg, "LAB8_IMAGE_ARCH", "amd64")
LOG_GROUP = require(cfg, "LAB8_LOG_GROUP")

# Docker reports amd64/arm64; ECS wants X86_64/ARM64.
ARCH_MAP = {"amd64": "X86_64", "x86_64": "X86_64", "arm64": "ARM64", "aarch64": "ARM64"}

ecs = boto3.client("ecs", region_name=AWS_REGION)
ec2 = boto3.client("ec2", region_name=AWS_REGION)
logs = boto3.client("logs", region_name=AWS_REGION)
sts = boto3.client("sts", region_name=AWS_REGION)


# --------------------------------------------------------------- networking
def default_vpc_id() -> str:
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        sys.exit(f"No default VPC in {AWS_REGION}. Create one, or pass a VpcId explicitly.")
    return vpcs[0]["VpcId"]


def pick_subnets(vpc_id: str) -> list:
    """Up to three subnets in real Availability Zones.

    zone-type is filtered explicitly: ap-southeast-2 exposes the Auckland Local
    Zone (ap-southeast-2-akl-1a), whose subnets do not support Fargate and fail
    the RunTask with a capacity error that reads like a quota problem.
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

    subnets = ec2.describe_subnets(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "availability-zone", "Values": zones},
        ]
    )["Subnets"]
    if not subnets:
        sys.exit(f"No usable subnet in {vpc_id}.")

    # A Fargate task with assignPublicIp=ENABLED still needs a subnet whose
    # route table reaches an internet gateway; the default VPC's subnets do.
    subnets.sort(key=lambda s: (not s.get("MapPublicIpOnLaunch", False), s["AvailabilityZone"]))
    chosen = subnets[:MAX_SUBNETS]
    for s in chosen:
        print(f"  subnet {s['SubnetId']} in {s['AvailabilityZone']}")
    return [s["SubnetId"] for s in chosen]


def ensure_security_group(vpc_id: str) -> str:
    """Inbound TCP on the Jupyter port, outbound HTTPS 443."""
    existing = ec2.describe_security_groups(
        Filters=[{"Name": "group-name", "Values": [SG_NAME]},
                 {"Name": "vpc-id", "Values": [vpc_id]}]
    )["SecurityGroups"]

    if existing:
        sg = existing[0]
        sg_id = sg["GroupId"]
        print(f"Security group {SG_NAME} already exists: {sg_id}")
    else:
        sg_id = ec2.create_security_group(
            GroupName=SG_NAME,
            Description="CITS5503 Lab 8 - inbound Jupyter, outbound HTTPS",
            VpcId=vpc_id,
        )["GroupId"]
        print(f"Created security group {SG_NAME}: {sg_id}")
        sg = ec2.describe_security_groups(GroupIds=[sg_id])["SecurityGroups"][0]

    try:
        ec2.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[{
                "IpProtocol": "tcp",
                "FromPort": JUPYTER_PORT,
                "ToPort": JUPYTER_PORT,
                "IpRanges": [{"CidrIp": OPEN_CIDR, "Description": "CITS5503 Lab 8 Jupyter"}],
            }],
        )
        print(f"  opened inbound TCP {JUPYTER_PORT} to {OPEN_CIDR}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "InvalidPermission.Duplicate":
            print(f"  inbound TCP {JUPYTER_PORT} already open")
        else:
            raise

    # A new group already carries an allow-all egress rule, which covers 443.
    # Only add an explicit 443 rule if that default has been removed, otherwise
    # the call fails as a duplicate on every re-run.
    egress = sg.get("IpPermissionsEgress", [])
    allows_all = any(p.get("IpProtocol") == "-1" for p in egress)
    allows_443 = any(
        p.get("IpProtocol") == "tcp"
        and p.get("FromPort", 0) <= HTTPS_PORT <= p.get("ToPort", 0)
        for p in egress
    )
    if allows_all:
        print(f"  outbound covered by the default allow-all rule (includes {HTTPS_PORT})")
    elif allows_443:
        print(f"  outbound TCP {HTTPS_PORT} already open")
    else:
        ec2.authorize_security_group_egress(
            GroupId=sg_id,
            IpPermissions=[{
                "IpProtocol": "tcp",
                "FromPort": HTTPS_PORT,
                "ToPort": HTTPS_PORT,
                "IpRanges": [{"CidrIp": OPEN_CIDR, "Description": "CITS5503 Lab 8 HTTPS"}],
            }],
        )
        print(f"  opened outbound TCP {HTTPS_PORT} to {OPEN_CIDR}")

    return sg_id


# ------------------------------------------------------------- task and log
def ensure_log_group() -> bool:
    """Create the CloudWatch group so a failing container leaves a trace.

    Not fatal: if the account denies logs:CreateLogGroup the deployment still
    works, it is just harder to debug, so fall back to no log configuration.
    """
    try:
        logs.create_log_group(logGroupName=LOG_GROUP)
        print(f"Created log group {LOG_GROUP}")
        return True
    except logs.exceptions.ResourceAlreadyExistsException:
        print(f"Log group {LOG_GROUP} already exists")
        return True
    except ClientError as e:
        print(f"Could not create {LOG_GROUP} ({e.response['Error']['Code']}) "
              "- continuing without container logs")
        return False


def create_ecs_task_definition(account_id: str, image_uri: str, use_logs: bool) -> dict:
    task_role_arn = f"arn:aws:iam::{account_id}:role/{TASK_ROLE}"
    execution_role_arn = f"arn:aws:iam::{account_id}:role/{EXEC_ROLE}"

    # The lab sheet's environment field: values the container reads at start-up.
    environment = {
        "JUPYTER_TOKEN": JUPYTER_TOKEN,
        "JUPYTER_PORT": str(JUPYTER_PORT),
        "AWS_DEFAULT_REGION": AWS_REGION,
    }

    container = {
        "name": CONTAINER,
        "image": image_uri,
        "essential": True,
        "portMappings": [{
            "containerPort": JUPYTER_PORT,
            "hostPort": JUPYTER_PORT,
            "protocol": "tcp",
        }],
        "environment": [{"name": k, "value": v} for k, v in environment.items()],
    }
    if use_logs:
        container["logConfiguration"] = {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": LOG_GROUP,
                "awslogs-region": AWS_REGION,
                "awslogs-stream-prefix": "ecs",
                "awslogs-create-group": "true",
            },
        }

    params = {
        "family": FAMILY,
        "networkMode": NETWORK_MODE,
        "requiresCompatibilities": [LAUNCH_TYPE],
        "cpu": TASK_CPU,
        "memory": TASK_MEMORY,
        "taskRoleArn": task_role_arn,
        "executionRoleArn": execution_role_arn,
        "containerDefinitions": [container],
    }

    # An image built on Apple Silicon will not run on the default x86_64
    # Fargate platform. lab8_image.sh records what was actually built.
    cpu_arch = ARCH_MAP.get(IMAGE_ARCH.lower())
    if cpu_arch is None:
        sys.exit(f"LAB8_IMAGE_ARCH={IMAGE_ARCH} is not one of {sorted(ARCH_MAP)}.")
    params["runtimePlatform"] = {"cpuArchitecture": cpu_arch, "operatingSystemFamily": "LINUX"}

    response = ecs.register_task_definition(**params)
    td = response["taskDefinition"]
    print(f"Registered task definition {td['family']}:{td['revision']} ({cpu_arch})")
    return td


# ----------------------------------------------------------------- cluster
def create_ecs_cluster(cluster_name: str) -> str:
    """create_cluster returns the existing cluster if it is already there."""
    arn = ecs.create_cluster(clusterName=cluster_name)["cluster"]["clusterArn"]
    print(f"Cluster {cluster_name}: {arn}")
    return arn


# ----------------------------------------------------------------- service
def live_service(cluster_name: str, service_name: str):
    described = ecs.describe_services(cluster=cluster_name, services=[service_name])["services"]
    for svc in described:
        if svc["status"] != "INACTIVE":
            return svc
    return None


def create_ecs_service(cluster_name, service_name, task_definition, subnet_ids, sg_ids) -> dict:
    network = {
        "awsvpcConfiguration": {
            "subnets": subnet_ids,
            "securityGroups": sg_ids,
            "assignPublicIp": "ENABLED",
        }
    }

    existing = live_service(cluster_name, service_name)
    if existing:
        print(f"Service {service_name} already exists - updating it in place")
        return ecs.update_service(
            cluster=cluster_name,
            service=service_name,
            taskDefinition=task_definition,
            desiredCount=DESIRED_COUNT,
            networkConfiguration=network,
            forceNewDeployment=True,
        )["service"]

    return ecs.create_service(
        cluster=cluster_name,
        serviceName=service_name,
        taskDefinition=task_definition,
        desiredCount=DESIRED_COUNT,
        launchType=LAUNCH_TYPE,
        networkConfiguration=network,
        deploymentConfiguration={"maximumPercent": 200, "minimumHealthyPercent": 100},
    )["service"]


def wait_for_service_stability(cluster_name: str, service_name: str) -> None:
    waiter = ecs.get_waiter("services_stable")
    try:
        waiter.wait(cluster=cluster_name, services=[service_name])
    except Exception:
        svc = live_service(cluster_name, service_name)
        if svc:
            print("\nThe service did not stabilise. Most recent events:")
            for ev in svc.get("events", [])[:5]:
                print(f"  {ev['createdAt']:%H:%M:%S}  {ev['message']}")
            print(f"\nContainer logs, if the log group was created:")
            print(f"  aws logs tail {LOG_GROUP} --region {AWS_REGION} --since 20m")
        raise


# --------------------------------------------------------------- public IP
def public_ip(cluster_name: str, service_name: str, tries: int = 10) -> str:
    """Task -> ENI -> public IPv4, the boto3 form of the lab sheet's CLI chain."""
    for attempt in range(tries):
        arns = ecs.list_tasks(cluster=cluster_name, serviceName=service_name,
                              desiredStatus="RUNNING")["taskArns"]
        if arns:
            task = ecs.describe_tasks(cluster=cluster_name, tasks=arns[:1])["tasks"][0]
            eni = None
            for attachment in task.get("attachments", []):
                for detail in attachment.get("details", []):
                    if detail["name"] == "networkInterfaceId":
                        eni = detail["value"]
            if eni:
                nic = ec2.describe_network_interfaces(
                    NetworkInterfaceIds=[eni])["NetworkInterfaces"][0]
                ip = nic.get("Association", {}).get("PublicIp", "")
                if ip:
                    return ip
        if attempt < tries - 1:
            time.sleep(6)
    return ""


# ----------------------------------------------------------------- cleanup
def cleanup() -> None:
    if live_service(CLUSTER, SERVICE):
        print(f"Scaling {SERVICE} to 0 and deleting it")
        ecs.update_service(cluster=CLUSTER, service=SERVICE, desiredCount=0)
        ecs.delete_service(cluster=CLUSTER, service=SERVICE, force=True)
        print("Waiting for the service to go inactive")
        ecs.get_waiter("services_inactive").wait(cluster=CLUSTER, services=[SERVICE])
        print("  service deleted")
    else:
        print(f"No live service named {SERVICE}")

    for arn in ecs.list_task_definitions(familyPrefix=FAMILY, status="ACTIVE")["taskDefinitionArns"]:
        ecs.deregister_task_definition(taskDefinition=arn)
        print(f"  deregistered {arn.rsplit('/', 1)[-1]}")

    try:
        ecs.delete_cluster(cluster=CLUSTER)
        print(f"Deleted cluster {CLUSTER}")
    except ecs.exceptions.ClusterNotFoundException:
        print(f"Cluster {CLUSTER} does not exist")

    update_env({"LAB8_TASK_DEF_ARN": "", "LAB8_PUBLIC_IP": "", "LAB8_JUPYTER_URL": ""})
    print("\nCleared the Lab 8 runtime keys in lab.env.")
    print("The security group and the ECR repository were left in place.")
    print("Remove those with:  python3 lab8_cleanup.py --all")


# --------------------------------------------------------------------- run
def main() -> None:
    ap = argparse.ArgumentParser(description="Deploy the Lab 8 image onto ECS Fargate.")
    ap.add_argument("--ip-only", action="store_true",
                    help="re-read the running task's public IP and stop")
    ap.add_argument("--no-logs", action="store_true",
                    help="do not attach a CloudWatch log group to the task")
    ap.add_argument("--cleanup", action="store_true",
                    help="delete the service and the cluster")
    args = ap.parse_args()

    if args.ip_only:
        ip = public_ip(CLUSTER, SERVICE)
        if not ip:
            sys.exit(f"No running task with a public IP in {CLUSTER}/{SERVICE}.")
        url = f"http://{ip}:{JUPYTER_PORT}/?token={JUPYTER_TOKEN}"
        update_env({"LAB8_PUBLIC_IP": ip, "LAB8_JUPYTER_URL": url})
        print(ip)
        print(f"\n{url}")
        return

    account_id = optional(cfg, "AWS_ACCOUNT_ID", "") or sts.get_caller_identity()["Account"]

    report({
        "LAB_PREFIX": LAB_PREFIX,
        "AWS_REGION": AWS_REGION,
        "AWS_ACCOUNT_ID": account_id,
        "Image": IMAGE_URI,
        "Image architecture": IMAGE_ARCH,
        "Cluster": CLUSTER,
        "Service": SERVICE,
        "Task family": FAMILY,
        "Container": CONTAINER,
        "Task role": TASK_ROLE,
        "Execution role": EXEC_ROLE,
        "CPU / memory": f"{TASK_CPU} / {TASK_MEMORY}",
        "Jupyter port": JUPYTER_PORT,
    })

    if args.cleanup:
        cleanup()
        return

    set_if_absent(cfg, {"AWS_ACCOUNT_ID": account_id})

    vpc_id = default_vpc_id()
    print(f"Default VPC {vpc_id}")
    subnet_ids = pick_subnets(vpc_id)
    sg_id = ensure_security_group(vpc_id)
    print()

    use_logs = (not args.no_logs) and ensure_log_group()
    td = create_ecs_task_definition(account_id, IMAGE_URI, use_logs)
    td_arn = td["taskDefinitionArn"]
    print()

    create_ecs_cluster(CLUSTER)
    svc = create_ecs_service(CLUSTER, SERVICE, td_arn, subnet_ids, [sg_id])
    print(f"ECS service: {svc['serviceArn']}")
    print()

    update_env({
        "LAB8_VPC_ID": vpc_id,
        "LAB8_SUBNET_IDS": ",".join(subnet_ids),
        "LAB8_SG_ID": sg_id,
        "LAB8_TASK_DEF_ARN": td_arn,
        "LAB8_LOGS_ENABLED": "yes" if use_logs else "no",
    })

    print(f"Waiting for {SERVICE} to become stable "
          "(pulling a ~2 GB image, so give it a few minutes)")
    wait_for_service_stability(CLUSTER, SERVICE)
    print(f"Service {SERVICE} is now stable.")
    print()

    ip = public_ip(CLUSTER, SERVICE)
    if not ip:
        print("The service is stable but no public IP appeared yet.")
        print("Re-read it with:  python3 lab8_ecs.py --ip-only")
        return

    url = f"http://{ip}:{JUPYTER_PORT}/?token={JUPYTER_TOKEN}"
    update_env({"LAB8_PUBLIC_IP": ip, "LAB8_JUPYTER_URL": url})

    print(f"Public IP: {ip}")
    print(f"\nOpen the notebook at:\n  {url}\n")
    print("The token is the one from the Dockerfile, so the plain "
          f"http://{ip}:{JUPYTER_PORT} page will prompt for it.")
    print(f"\nNext:  python3 lab8_bucket.py, then run the notebook.")


if __name__ == "__main__":
    main()
