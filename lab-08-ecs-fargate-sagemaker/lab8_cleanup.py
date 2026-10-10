#!/usr/bin/env python3
"""CITS5503 Lab 8 - tear down the ECS, ECR and S3 resources.

The cleanup checkpoint asks for three deletions, in order: ECR, then ECS, then
empty and delete S3. --all follows that order, with two additions it does not
mention: the security group goes straight after ECS (it cannot be deleted until
the task's network interface has been released, which happens asynchronously),
and the CloudWatch log group goes last.

Run with the shared venv active:
    labenv
    python3 lab8_cleanup.py --all

    python3 lab8_cleanup.py --ecs      service, cluster, task definitions
    python3 lab8_cleanup.py --sg       security group
    python3 lab8_cleanup.py --ecr      repository and its images
    python3 lab8_cleanup.py --s3       bucket and its contents
    python3 lab8_cleanup.py --logs     CloudWatch log group

Nothing here touches the SageMaker tuning job: AWS keeps completed tuning jobs
as a permanent record and there is no delete API for them. Its training output
lives in the S3 bucket, which --s3 removes.
"""

import argparse
import sys
import time

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    sys.exit("boto3 not found. Activate the shared lab venv first:  labenv")

from lab8_env import load_env, require, optional, update_env, report

cfg = load_env()
LAB_PREFIX, AWS_REGION = require(cfg, "LAB_PREFIX", "AWS_REGION")
CLUSTER, SERVICE, FAMILY = require(
    cfg, "LAB8_CLUSTER", "LAB8_SERVICE_NAME", "LAB8_TASK_FAMILY")
SG_NAME = require(cfg, "LAB8_SG_NAME")
REPO = require(cfg, "LAB8_ECR_REPO")
BUCKET = require(cfg, "LAB8_BUCKET")
LOG_GROUP = require(cfg, "LAB8_LOG_GROUP")

ecs = boto3.client("ecs", region_name=AWS_REGION)
ec2 = boto3.client("ec2", region_name=AWS_REGION)
ecr = boto3.client("ecr", region_name=AWS_REGION)
s3 = boto3.resource("s3", region_name=AWS_REGION)
s3c = boto3.client("s3", region_name=AWS_REGION)
logs = boto3.client("logs", region_name=AWS_REGION)


def drop_ecs() -> None:
    print("ECS")
    described = ecs.describe_services(cluster=CLUSTER, services=[SERVICE])["services"] \
        if cluster_exists() else []
    live = [s for s in described if s["status"] != "INACTIVE"]

    if live:
        ecs.update_service(cluster=CLUSTER, service=SERVICE, desiredCount=0)
        ecs.delete_service(cluster=CLUSTER, service=SERVICE, force=True)
        print(f"  deleting service {SERVICE} - waiting for it to go inactive")
        ecs.get_waiter("services_inactive").wait(cluster=CLUSTER, services=[SERVICE])
        print("  service deleted")
    else:
        print(f"  no live service named {SERVICE}")

    arns = ecs.list_task_definitions(familyPrefix=FAMILY, status="ACTIVE")["taskDefinitionArns"]
    for arn in arns:
        ecs.deregister_task_definition(taskDefinition=arn)
        print(f"  deregistered {arn.rsplit('/', 1)[-1]}")
    if not arns:
        print(f"  no active task definitions in family {FAMILY}")

    try:
        ecs.delete_cluster(cluster=CLUSTER)
        print(f"  deleted cluster {CLUSTER}")
    except ecs.exceptions.ClusterNotFoundException:
        print(f"  cluster {CLUSTER} does not exist")

    update_env({"LAB8_TASK_DEF_ARN": "", "LAB8_PUBLIC_IP": "", "LAB8_JUPYTER_URL": ""})


def cluster_exists() -> bool:
    found = ecs.describe_clusters(clusters=[CLUSTER])["clusters"]
    return any(c["status"] != "INACTIVE" for c in found)


def drop_security_group() -> None:
    print("Security group")
    groups = ec2.describe_security_groups(
        Filters=[{"Name": "group-name", "Values": [SG_NAME]}])["SecurityGroups"]
    if not groups:
        print(f"  {SG_NAME} does not exist")
        update_env({"LAB8_SG_ID": ""})
        return

    sg_id = groups[0]["GroupId"]
    # The task's ENI is detached asynchronously after the service goes inactive,
    # so a delete straight after ECS teardown fails with DependencyViolation.
    for attempt in range(12):
        try:
            ec2.delete_security_group(GroupId=sg_id)
            print(f"  deleted {SG_NAME} ({sg_id})")
            update_env({"LAB8_SG_ID": ""})
            return
        except ClientError as e:
            if e.response["Error"]["Code"] != "DependencyViolation":
                raise
            if attempt == 0:
                print("  still attached to a network interface - waiting")
            time.sleep(10)
    print(f"  could not delete {SG_NAME} ({sg_id}) - a network interface is still using it.")
    print("  Re-run this in a couple of minutes, or delete it from the console.")


def drop_ecr() -> None:
    print("ECR")
    try:
        ecr.delete_repository(repositoryName=REPO, force=True)
        print(f"  deleted repository {REPO} and every image in it")
    except ecr.exceptions.RepositoryNotFoundException:
        print(f"  repository {REPO} does not exist")
    update_env({"LAB8_ECR_URI": "", "LAB8_ECR_REGISTRY": "", "LAB8_IMAGE_URI": ""})


def drop_s3() -> None:
    print("S3")
    try:
        s3c.head_bucket(Bucket=BUCKET)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchBucket"):
            print(f"  bucket {BUCKET} does not exist")
            return
        raise

    bucket = s3.Bucket(BUCKET)
    deleted = sum(1 for _ in bucket.objects.all())
    bucket.objects.all().delete()
    # Versioned buckets keep delete markers that block the bucket delete.
    try:
        bucket.object_versions.all().delete()
    except ClientError:
        pass
    print(f"  emptied {BUCKET} ({deleted} object(s))")
    s3c.delete_bucket(Bucket=BUCKET)
    print(f"  deleted bucket {BUCKET}")


def drop_logs() -> None:
    print("CloudWatch Logs")
    try:
        logs.delete_log_group(logGroupName=LOG_GROUP)
        print(f"  deleted log group {LOG_GROUP}")
    except logs.exceptions.ResourceNotFoundException:
        print(f"  log group {LOG_GROUP} does not exist")
    except ClientError as e:
        print(f"  could not delete {LOG_GROUP}: {e.response['Error']['Code']}")
    update_env({"LAB8_LOGS_ENABLED": "no"})


def main() -> None:
    ap = argparse.ArgumentParser(description="Delete the Lab 8 AWS resources.")
    ap.add_argument("--all", action="store_true", help="everything, in dependency order")
    ap.add_argument("--ecs", action="store_true")
    ap.add_argument("--sg", action="store_true")
    ap.add_argument("--ecr", action="store_true")
    ap.add_argument("--s3", action="store_true")
    ap.add_argument("--logs", action="store_true")
    ap.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    args = ap.parse_args()

    steps = []
    if args.all:
        # The order the checkpoint asks for in the console: ECR, then ECS, then
        # S3. The security group goes straight after ECS, because it cannot be
        # deleted until the task's network interface has been released.
        steps = [drop_ecr, drop_ecs, drop_security_group, drop_s3, drop_logs]
    else:
        if args.ecs:
            steps.append(drop_ecs)
        if args.sg:
            steps.append(drop_security_group)
        if args.ecr:
            steps.append(drop_ecr)
        if args.s3:
            steps.append(drop_s3)
        if args.logs:
            steps.append(drop_logs)

    if not steps:
        ap.print_help()
        sys.exit(2)

    report({
        "AWS_REGION": AWS_REGION,
        "Cluster / service": f"{CLUSTER} / {SERVICE}",
        "Task family": FAMILY,
        "Security group": SG_NAME,
        "ECR repository": REPO,
        "S3 bucket": BUCKET,
        "Log group": LOG_GROUP,
    })

    if not args.yes:
        print("This deletes the resources listed above. Anything the tuning job")
        print(f"wrote to s3://{BUCKET}/ goes with it.")
        if input("Type 'delete' to continue: ").strip() != "delete":
            sys.exit("Cancelled.")
        print()

    for step in steps:
        step()
        print()

    print("Done. Confirm in the console: ECS -> Clusters, ECR -> Repositories, S3 -> Buckets.")


if __name__ == "__main__":
    main()
