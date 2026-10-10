#!/usr/bin/env python3
"""
CITS5503 Lab 6 - Application Load Balancer
------------------------------------------
Creates, using the VPC and subnet the Lab 6 instance already lives in:

  * a target group <prefix>-tg-lab6 (HTTP:80), health check path /polls/
    with a 30 second interval, as Checkpoint 2 requires
  * the instance registered as a target
  * an internet-facing ALB <prefix>-alb-lab6 spanning two subnets in two AZs
    (an ALB will not start with only one)
  * an HTTP:80 listener whose default action forwards to the target group

Idempotent: existing resources are reused, and the health check settings on an
existing target group are corrected rather than left as they were.

    labenv
    python3 lab6_alb.py
"""

import sys
import time

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    sys.exit("boto3 not found. Activate the shared lab venv first:  labenv")

from lab6_env import load_env, require, update_env, report

# Fixed by Checkpoint 2.
HEALTH_PATH = "/polls/"
HEALTH_INTERVAL = 30
HEALTH_TIMEOUT = 5
HEALTHY_THRESHOLD = 2
UNHEALTHY_THRESHOLD = 2
TARGET_PORT = 80          # nginx on the instance, which proxies to Django on 8000

cfg = load_env()
LAB_PREFIX, AWS_REGION = require(cfg, "LAB_PREFIX", "AWS_REGION")
VPC_ID, SUBNET_ID, SG_ID, INSTANCE_ID = require(
    cfg, "LAB6_VPC_ID", "LAB6_SUBNET_ID", "LAB6_SG_ID", "LAB6_VM1_ID"
)

TG_NAME = f"{LAB_PREFIX}-tg-lab6"
ALB_NAME = f"{LAB_PREFIX}-alb-lab6"

report({
    "AWS_REGION": AWS_REGION,
    "VPC": VPC_ID,
    "Instance": INSTANCE_ID,
    "Target group": f"{TG_NAME}  (HTTP:{TARGET_PORT})",
    "Load balancer": ALB_NAME,
    "Health check": f"{HEALTH_PATH} every {HEALTH_INTERVAL}s",
})

ec2 = boto3.client("ec2", region_name=AWS_REGION)
elb = boto3.client("elbv2", region_name=AWS_REGION)


def choose_subnets() -> list:
    """The instance's subnet plus one more, in a different AZ."""
    subnets = ec2.describe_subnets(
        Filters=[{"Name": "vpc-id", "Values": [VPC_ID]}]
    )["Subnets"]
    by_id = {s["SubnetId"]: s for s in subnets}

    if SUBNET_ID not in by_id:
        sys.exit(f"Subnet {SUBNET_ID} is not in VPC {VPC_ID}. Re-run lab6_ec2.py.")

    primary = by_id[SUBNET_ID]
    for subnet in subnets:
        if subnet["AvailabilityZone"] != primary["AvailabilityZone"]:
            chosen = [primary, subnet]
            print(f"Subnets: {primary['SubnetId']} ({primary['AvailabilityZone']}), "
                  f"{subnet['SubnetId']} ({subnet['AvailabilityZone']})")
            return [s["SubnetId"] for s in chosen]

    sys.exit(
        f"Only one availability zone has a subnet in {VPC_ID}. "
        "An ALB needs subnets in at least two AZs."
    )


def ensure_target_group() -> str:
    try:
        tg = elb.describe_target_groups(Names=[TG_NAME])["TargetGroups"][0]
        arn = tg["TargetGroupArn"]
        print(f"Target group {TG_NAME} already exists")
        # Force the checkpoint's health check settings even on a pre-existing group.
        elb.modify_target_group(
            TargetGroupArn=arn,
            HealthCheckProtocol="HTTP",
            HealthCheckPath=HEALTH_PATH,
            HealthCheckIntervalSeconds=HEALTH_INTERVAL,
            HealthCheckTimeoutSeconds=HEALTH_TIMEOUT,
            HealthyThresholdCount=HEALTHY_THRESHOLD,
            UnhealthyThresholdCount=UNHEALTHY_THRESHOLD,
            Matcher={"HttpCode": "200"},
        )
        print(f"  health check set to {HEALTH_PATH} every {HEALTH_INTERVAL}s")
        return arn
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "TargetGroupNotFound":
            raise

    arn = elb.create_target_group(
        Name=TG_NAME,
        Protocol="HTTP",
        Port=TARGET_PORT,
        VpcId=VPC_ID,
        TargetType="instance",
        HealthCheckProtocol="HTTP",
        HealthCheckPath=HEALTH_PATH,
        HealthCheckIntervalSeconds=HEALTH_INTERVAL,
        HealthCheckTimeoutSeconds=HEALTH_TIMEOUT,
        HealthyThresholdCount=HEALTHY_THRESHOLD,
        UnhealthyThresholdCount=UNHEALTHY_THRESHOLD,
        Matcher={"HttpCode": "200"},
    )["TargetGroups"][0]["TargetGroupArn"]
    print(f"Created target group {TG_NAME}")
    print(f"  health check {HEALTH_PATH} every {HEALTH_INTERVAL}s")
    return arn


def register_target(tg_arn: str) -> None:
    registered = {
        t["Target"]["Id"]
        for t in elb.describe_target_health(TargetGroupArn=tg_arn)["TargetHealthDescriptions"]
    }
    if INSTANCE_ID in registered:
        print(f"Instance {INSTANCE_ID} is already a registered target")
        return
    elb.register_targets(TargetGroupArn=tg_arn, Targets=[{"Id": INSTANCE_ID, "Port": TARGET_PORT}])
    print(f"Registered {INSTANCE_ID} in {TG_NAME}")


def ensure_load_balancer(subnet_ids: list) -> dict:
    try:
        lb = elb.describe_load_balancers(Names=[ALB_NAME])["LoadBalancers"][0]
        print(f"Load balancer {ALB_NAME} already exists ({lb['State']['Code']})")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "LoadBalancerNotFound":
            raise
        lb = elb.create_load_balancer(
            Name=ALB_NAME,
            Subnets=subnet_ids,
            SecurityGroups=[SG_ID],
            Scheme="internet-facing",
            Type="application",
            IpAddressType="ipv4",
            Tags=[{"Key": "Name", "Value": ALB_NAME}],
        )["LoadBalancers"][0]
        print(f"Created load balancer {ALB_NAME}")

    if lb["State"]["Code"] != "active":
        print("Waiting for the load balancer to become active (a few minutes) ...")
        elb.get_waiter("load_balancer_available").wait(LoadBalancerArns=[lb["LoadBalancerArn"]])
        lb = elb.describe_load_balancers(Names=[ALB_NAME])["LoadBalancers"][0]
    print(f"  state {lb['State']['Code']}, {lb['Scheme']}, DNS {lb['DNSName']}")
    return lb


def ensure_listener(lb_arn: str, tg_arn: str) -> str:
    listeners = elb.describe_listeners(LoadBalancerArn=lb_arn)["Listeners"]
    for listener in listeners:
        if listener["Port"] == 80 and listener["Protocol"] == "HTTP":
            forwards_here = any(
                action.get("TargetGroupArn") == tg_arn
                for action in listener.get("DefaultActions", [])
            )
            if not forwards_here:
                elb.modify_listener(
                    ListenerArn=listener["ListenerArn"],
                    DefaultActions=[{"Type": "forward", "TargetGroupArn": tg_arn}],
                )
                print("Updated the HTTP:80 listener to forward to the target group")
            else:
                print("HTTP:80 listener already forwards to the target group")
            return listener["ListenerArn"]

    arn = elb.create_listener(
        LoadBalancerArn=lb_arn,
        Protocol="HTTP",
        Port=80,
        DefaultActions=[{"Type": "forward", "TargetGroupArn": tg_arn}],
    )["Listeners"][0]["ListenerArn"]
    print("Created the HTTP:80 listener")
    return arn


def wait_for_health(tg_arn: str, minutes: int = 5) -> str:
    """Poll until the target reports healthy, or give up and report what it says."""
    print(f"\nWaiting for {INSTANCE_ID} to report healthy (up to {minutes} min) ...")
    deadline = time.time() + minutes * 60
    state, reason = "unknown", ""
    while time.time() < deadline:
        for desc in elb.describe_target_health(TargetGroupArn=tg_arn)["TargetHealthDescriptions"]:
            if desc["Target"]["Id"] != INSTANCE_ID:
                continue
            state = desc["TargetHealth"]["State"]
            reason = desc["TargetHealth"].get("Description", "")
        if state == "healthy":
            print(f"  target health: {state}")
            return state
        print(f"  target health: {state} {reason}".rstrip(), end="\r")
        time.sleep(15)

    print(f"\n  target health: {state} {reason}".rstrip())
    print("  Not healthy yet. Common causes: nginx or the Django service is down on the")
    print("  instance, or the security group does not allow tcp/80 from the ALB.")
    return state


def main() -> None:
    subnet_ids = choose_subnets()
    tg_arn = ensure_target_group()
    register_target(tg_arn)
    lb = ensure_load_balancer(subnet_ids)
    listener_arn = ensure_listener(lb["LoadBalancerArn"], tg_arn)
    wait_for_health(tg_arn)

    update_env({
        "LAB6_TG_NAME": TG_NAME,
        "LAB6_TG_ARN": tg_arn,
        "LAB6_ALB_NAME": ALB_NAME,
        "LAB6_ALB_ARN": lb["LoadBalancerArn"],
        "LAB6_ALB_DNS": lb["DNSName"],
        "LAB6_LISTENER_ARN": listener_arn,
    })

    print()
    print("=" * 62)
    print(f"  Load balancer  {ALB_NAME}")
    print(f"  DNS            {lb['DNSName']}")
    print(f"  Target group   {TG_NAME}  {HEALTH_PATH} every {HEALTH_INTERVAL}s")
    print("=" * 62)
    print(f"\n  Open in a browser:  http://{lb['DNSName']}/polls/")
    print("\nNext:  ./lab6_verify.sh")


if __name__ == "__main__":
    main()
