#!/usr/bin/env python3
"""
CITS5503 Lab 6 - AWS DynamoDB table for the CloudStorage web interface
----------------------------------------------------------------------
Checkpoint 3 needs two tables holding the same records:

  * the DynamoDB Local table from Lab 3 (scanned live from your terminal)
  * an AWS DynamoDB table (scanned by the Django view and rendered at /polls/)

Default mode copies Local -> AWS:

    ./dynamodb_local.sh start          # from the Lab 3 directory
    python3 lab6_ddb_sync.py

If the Lab 3 data is gone, there are two fallbacks:

    python3 lab6_ddb_sync.py --from-s3      # bucket still exists
    python3 lab6_ddb_sync.py --from-tree    # bucket deleted at Lab 3/4 cleanup

Both write the records to DynamoDB Local AND to AWS, so the live local scan in
Checkpoint 3 still has something to return. --from-tree needs no S3 at all: it
reads the local rootdir/ tree (recreating it with the Lab 3 contents if it is
missing) and takes the owner identity from your AWS account. Lab 6 is not
assessed on the bucket, only on the two tables agreeing with the web page.

Table names and the local endpoint come from lab.env (DDB_TABLE, DDB_PORT,
AWS_DDB_TABLE, BUCKET_NAME, ROOT_DIR); the AWS table name is written back so
the deploy and verify scripts agree with the Django view.
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import boto3
    from botocore.exceptions import ClientError, EndpointConnectionError
except ImportError:
    sys.exit("boto3 not found. Activate the shared lab venv first:  labenv")

from lab6_env import load_env, require, optional, update_env, report

# Fixed by the lab: userId is the partition key, fileName the sort key.
KEY_SCHEMA = [
    {"AttributeName": "userId", "KeyType": "HASH"},
    {"AttributeName": "fileName", "KeyType": "RANGE"},
]
ATTR_DEFS = [
    {"AttributeName": "userId", "AttributeType": "S"},
    {"AttributeName": "fileName", "AttributeType": "S"},
]

# AWS regions where get_object_acl returns a DisplayName for the owner.
DISPLAYNAME_REGIONS = {
    "us-east-1", "us-west-1", "us-west-2", "ap-southeast-1", "ap-southeast-2",
    "ap-northeast-1", "eu-west-1", "sa-east-1",
}

cfg = load_env()
LAB_PREFIX, AWS_REGION = require(cfg, "LAB_PREFIX", "AWS_REGION")

LOCAL_TABLE = optional(cfg, "DDB_TABLE", "CloudFiles")
DDB_PORT = optional(cfg, "DDB_PORT", "8000")
LOCAL_ENDPOINT = optional(cfg, "DDB_ENDPOINT", f"http://localhost:{DDB_PORT}")
AWS_TABLE = optional(cfg, "AWS_DDB_TABLE", "UserFiles")
ROOT_DIR = optional(cfg, "ROOT_DIR", "rootdir")
BUCKET_NAME = cfg.get("BUCKET_NAME") or f"{LAB_PREFIX}-cloudstorage"

# Where --from-tree looks for the Lab 3 directory tree, in order. Override with
# LAB6_TREE=/path/to/rootdir.
HERE = Path(__file__).resolve().parent
TREE_CANDIDATES = [
    Path(os.environ["LAB6_TREE"]).expanduser() if os.environ.get("LAB6_TREE") else None,
    HERE / ROOT_DIR,
    HERE.parent / "lab3" / ROOT_DIR,
    Path.home() / "cits5503" / "lab3" / ROOT_DIR,
]

# The file contents Lab 3 specifies, used only when the tree has to be recreated.
LAB3_FILE_BODY = "1\n2\n3\n4\n5\n"


def wait_active(table) -> None:
    table.meta.client.get_waiter("table_exists").wait(TableName=table.name)


def ensure_table(resource, name: str) -> object:
    """Create the table with the Lab 3 key schema if it is not there already."""
    table = resource.Table(name)
    try:
        table.load()
        print(f"  table {name} already exists ({table.item_count} item(s) reported)")
        return table
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    print(f"  creating table {name} ...")
    kwargs = dict(
        TableName=name,
        KeySchema=KEY_SCHEMA,
        AttributeDefinitions=ATTR_DEFS,
        BillingMode="PAY_PER_REQUEST",
    )
    try:
        table = resource.create_table(**kwargs)
    except ClientError as exc:
        # DynamoDB Local ignores BillingMode on older builds.
        if exc.response["Error"]["Code"] not in ("ValidationException", "UnknownOperationException"):
            raise
        kwargs.pop("BillingMode")
        kwargs["ProvisionedThroughput"] = {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5}
        table = resource.create_table(**kwargs)

    wait_active(table)
    print(f"  table {name} is active")
    return table


def scan_all(table) -> list:
    items, kwargs = [], {}
    while True:
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            return items
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def items_from_local() -> list:
    print(f"Reading DynamoDB Local table {LOCAL_TABLE} at {LOCAL_ENDPOINT}")
    local = boto3.resource(
        "dynamodb",
        endpoint_url=LOCAL_ENDPOINT,
        region_name=AWS_REGION,
        aws_access_key_id="local",
        aws_secret_access_key="local",
    )
    try:
        items = scan_all(local.Table(LOCAL_TABLE))
    except EndpointConnectionError:
        sys.exit(
            f"Cannot reach DynamoDB Local at {LOCAL_ENDPOINT}.\n"
            "Start it from the Lab 3 directory (./dynamodb_local.sh start),\n"
            "or rebuild both tables from S3 with:  python3 lab6_ddb_sync.py --from-s3"
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ResourceNotFoundException":
            sys.exit(
                f"Table {LOCAL_TABLE} does not exist on DynamoDB Local.\n"
                "Either re-run the Lab 3 write script, or use:  python3 lab6_ddb_sync.py --from-s3"
            )
        raise

    if not items:
        sys.exit(f"Table {LOCAL_TABLE} is empty - nothing to copy.")
    print(f"  {len(items)} item(s) read")
    return items


def items_from_s3() -> list:
    """Rebuild the Lab 3 records straight from the S3 bucket."""
    print(f"Rebuilding records from s3://{BUCKET_NAME}/{ROOT_DIR}/")
    s3 = boto3.client("s3", region_name=AWS_REGION)

    try:
        s3.head_bucket(Bucket=BUCKET_NAME)
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("404", "NoSuchBucket"):
            sys.exit(
                f"Bucket {BUCKET_NAME} does not exist - it was most likely deleted "
                "during the Lab 3 or Lab 4 cleanup.\n"
                "Lab 6 does not need it. Rebuild from the local tree instead:\n"
                "  python3 lab6_ddb_sync.py --from-tree"
            )
        if code in ("403", "AccessDenied"):
            sys.exit(
                f"Access to bucket {BUCKET_NAME} is denied. If the Lab 4 deny policy "
                "is still on it, either remove that policy or use:\n"
                "  python3 lab6_ddb_sync.py --from-tree"
            )
        raise

    paginator = s3.get_paginator("list_objects_v2")

    items = []
    for page in paginator.paginate(Bucket=BUCKET_NAME, Prefix=f"{ROOT_DIR}/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue

            acl = s3.get_object_acl(Bucket=BUCKET_NAME, Key=key)
            owner = acl["Owner"]
            owner_id = owner.get("ID", "")
            display = owner.get("DisplayName") or owner_id
            permissions = ",".join(
                sorted({g.get("Permission", "") for g in acl.get("Grants", [])})
            ) or "FULL_CONTROL"

            items.append({
                "userId": owner_id,
                "fileName": key.rsplit("/", 1)[-1],
                "path": key,
                "lastUpdated": obj["LastModified"].astimezone(timezone.utc)
                                  .strftime("%Y-%m-%d %H:%M:%S UTC"),
                "owner": display if AWS_REGION in DISPLAYNAME_REGIONS else owner_id,
                "permissions": permissions,
            })

    if not items:
        sys.exit(
            f"No objects under s3://{BUCKET_NAME}/{ROOT_DIR}/.\n"
            "Re-run the Lab 3 upload, or rebuild from the local tree:\n"
            "  python3 lab6_ddb_sync.py --from-tree"
        )
    print(f"  {len(items)} object(s) found")
    return items


def account_owner() -> tuple:
    """
    The canonical S3 owner of this account: (ID, DisplayName).

    list_buckets returns it without needing any bucket to exist, so this still
    works after the Lab 3 bucket has been deleted. Falls back to the IAM ARN if
    the call is denied.
    """
    try:
        owner = boto3.client("s3", region_name=AWS_REGION).list_buckets()["Owner"]
        return owner.get("ID", ""), owner.get("DisplayName") or owner.get("ID", "")
    except ClientError:
        arn = boto3.client("sts", region_name=AWS_REGION).get_caller_identity()["Arn"]
        return arn, arn.rsplit("/", 1)[-1]


def locate_tree() -> Path:
    """Find the Lab 3 rootdir tree, or recreate it with the Lab 3 contents."""
    for candidate in TREE_CANDIDATES:
        if candidate and candidate.is_dir():
            return candidate

    target = HERE / ROOT_DIR
    print(f"  no {ROOT_DIR}/ tree found - recreating it at {target}")
    (target / "subdir").mkdir(parents=True, exist_ok=True)
    (target / "rootfile.txt").write_text(LAB3_FILE_BODY)
    (target / "subdir" / "subfile.txt").write_text(LAB3_FILE_BODY)
    return target


def items_from_tree() -> list:
    """
    Rebuild the Lab 3 records from the local directory tree.

    Used when the S3 bucket has already been deleted. The attributes are the
    same six Lab 3 defines; owner and userId come from the account's canonical
    S3 owner rather than from a per-object ACL, since there are no objects.
    """
    tree = locate_tree()
    print(f"Rebuilding records from {tree}")

    owner_id, display_name = account_owner()
    owner = display_name if AWS_REGION in DISPLAYNAME_REGIONS else owner_id

    items = []
    for path in sorted(tree.rglob("*")):
        if not path.is_file():
            continue
        key = f"{ROOT_DIR}/{path.relative_to(tree).as_posix()}"
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        items.append({
            "userId": owner_id,
            "fileName": path.name,
            "path": key,
            "lastUpdated": modified.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "owner": owner,
            "permissions": "FULL_CONTROL",
        })

    if not items:
        sys.exit(f"No files under {tree} - nothing to record.")
    print(f"  {len(items)} file(s) found")
    return items


def write_items(table, items: list) -> None:
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)
    print(f"  wrote {len(items)} item(s) to {table.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Populate the AWS DynamoDB table for Lab 6.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--from-s3",
        action="store_true",
        help="rebuild the records from the S3 bucket (use when the Lab 3 local "
             "data is gone but the bucket still exists)",
    )
    source.add_argument(
        "--from-tree",
        action="store_true",
        help="rebuild the records from the local rootdir/ tree, recreating it if "
             "needed (use when the bucket has been deleted too)",
    )
    args = parser.parse_args()
    rebuilding = args.from_s3 or args.from_tree

    report({
        "AWS_REGION": AWS_REGION,
        "Local table": f"{LOCAL_TABLE}  ({LOCAL_ENDPOINT})",
        "AWS table": AWS_TABLE,
        "Source": ("S3 bucket " + BUCKET_NAME) if args.from_s3
                  else ("local tree " + ROOT_DIR + "/") if args.from_tree
                  else "DynamoDB Local",
    })

    if args.from_s3:
        items = items_from_s3()
    elif args.from_tree:
        items = items_from_tree()
    else:
        items = items_from_local()

    if rebuilding:
        print("\nDynamoDB Local")
        local = boto3.resource(
            "dynamodb",
            endpoint_url=LOCAL_ENDPOINT,
            region_name=AWS_REGION,
            aws_access_key_id="local",
            aws_secret_access_key="local",
        )
        try:
            write_items(ensure_table(local, LOCAL_TABLE), items)
        except EndpointConnectionError:
            print(f"  DynamoDB Local not reachable at {LOCAL_ENDPOINT} - skipped.")
            print("  Start it before the demo: the live local scan is part of Checkpoint 3.")

    print("\nAWS DynamoDB")
    aws = boto3.resource("dynamodb", region_name=AWS_REGION)
    write_items(ensure_table(aws, AWS_TABLE), items)

    update_env({"AWS_DDB_TABLE": AWS_TABLE})

    print("\nRecords now in both tables:")
    for item in items:
        print(f"  {item.get('fileName', '?'):<16} {item.get('path', '')}")
    print(f"\nThe Django view at /polls/ scans {AWS_TABLE} in {AWS_REGION}.")
    print("Next:  ./lab6_deploy.sh")


if __name__ == "__main__":
    main()
