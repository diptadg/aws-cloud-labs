#!/usr/bin/env python3
# ------------------------------
# CITS5503 - Lab 3
#
# write_to_dynamodb.py
#
# Creates the CloudFiles table on DynamoDB Local, then reads every object in
# the S3 bucket and writes one item per file:
#
#   userId       (partition key)  canonical user ID of the object owner
#   fileName     (sort key)       e.g. rootfile.txt
#   path                          e.g. rootdir/  or  rootdir/subdir/
#   lastUpdated                   S3 LastModified, ISO 8601
#   owner                         ACL DisplayName (falls back to ID)
#   permissions                   ACL grant permissions, comma separated
#
# DynamoDB Local must be running first:  ./dynamodb_local.sh start
# Run with the venv active:              labenv && python3 write_to_dynamodb.py
# ------------------------------

import os
import sys

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError


LAB_ENV = os.environ.get("LAB_ENV") or os.path.expanduser("~/cits5503/lab.env")


def load_lab_env(path=LAB_ENV, required=("LAB_PREFIX", "AWS_REGION")):
    """Read lab.env, written by the Lab 1 setup script. Nothing is defaulted."""
    values = {}
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                values[key.strip()] = val.strip().strip('"').strip("'")
    except FileNotFoundError:
        sys.exit(
            f"ERROR: {path} not found.\n"
            "It is created by the Lab 1 setup script - run that first."
        )

    missing = [k for k in required if not values.get(k)]
    if missing:
        sys.exit(f"ERROR: {', '.join(missing)} not set in {path}.")
    return values


_env = load_lab_env()
LAB_PREFIX = _env["LAB_PREFIX"]
REGION = _env["AWS_REGION"]
BUCKET = _env.get("BUCKET_NAME") or f"{LAB_PREFIX}-cloudstorage"

TABLE_NAME = os.environ.get("DDB_TABLE", "CloudFiles")

# Port and endpoint are overridable, and DDB_PORT is the same knob the shell
# scripts use, so changing it in one place moves all of them together.
DDB_PORT = os.environ.get("DDB_PORT", "8000")
DDB_ENDPOINT = os.environ.get("DDB_ENDPOINT", f"http://localhost:{DDB_PORT}")

# DisplayName is only returned in a subset of regions. ap-southeast-2 (Sydney)
# is one of them, so 'owner' gets the display name; anywhere else we fall back
# to the canonical ID, which is the same value as userId.
DISPLAYNAME_REGIONS = {
    "us-east-1", "us-west-1", "us-west-2", "ap-southeast-1",
    "ap-southeast-2", "ap-northeast-1", "eu-west-1", "sa-east-1",
}

s3 = boto3.client("s3", region_name=REGION)

# DynamoDB Local ignores credentials, but botocore still insists that some
# exist, so fall back to dummies if the profile is not resolvable.
session = boto3.Session()
ddb_kwargs = {"endpoint_url": DDB_ENDPOINT, "region_name": REGION}
if session.get_credentials() is None:
    ddb_kwargs.update(aws_access_key_id="dummy", aws_secret_access_key="dummy")
dynamodb = boto3.resource("dynamodb", **ddb_kwargs)


# ------------------------------------------------------------------
# Create the table (idempotent)
# ------------------------------------------------------------------
def create_table():
    client = dynamodb.meta.client
    try:
        existing = client.list_tables()["TableNames"]
    except EndpointConnectionError:
        sys.exit(
            f"ERROR: nothing listening on {DDB_ENDPOINT}.\n"
            "Start DynamoDB Local first:  ./dynamodb_local.sh start"
        )

    if TABLE_NAME in existing:
        print(f"Table {TABLE_NAME} already exists - reusing it")
        return dynamodb.Table(TABLE_NAME)

    print(f"Creating table {TABLE_NAME} ...")
    table = dynamodb.create_table(
        TableName=TABLE_NAME,
        KeySchema=[
            {"AttributeName": "userId", "KeyType": "HASH"},    # partition key
            {"AttributeName": "fileName", "KeyType": "RANGE"},  # sort key
        ],
        AttributeDefinitions=[
            {"AttributeName": "userId", "AttributeType": "S"},
            {"AttributeName": "fileName", "AttributeType": "S"},
        ],
        ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
    )
    table.wait_until_exists()
    print(f"Table {TABLE_NAME} created")
    return table


# ------------------------------------------------------------------
# Pull the attributes for one S3 object
# ------------------------------------------------------------------
def attributes_for(key):
    head = s3.head_object(Bucket=BUCKET, Key=key)
    acl = s3.get_object_acl(Bucket=BUCKET, Key=key)

    owner = acl.get("Owner", {})
    owner_id = owner.get("ID", "unknown")
    display_name = owner.get("DisplayName")

    if REGION in DISPLAYNAME_REGIONS and display_name:
        owner_value = display_name
    else:
        owner_value = owner_id

    permissions = sorted({g.get("Permission", "") for g in acl.get("Grants", []) if g.get("Permission")})

    file_name = key.rsplit("/", 1)[-1]
    path = key[: -len(file_name)] or "/"

    return {
        "userId": owner_id,
        "fileName": file_name,
        "path": path,
        "lastUpdated": head["LastModified"].isoformat(),
        "owner": owner_value,
        "permissions": ",".join(permissions) if permissions else "NONE",
    }


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------
def main():
    table = create_table()

    try:
        s3.head_bucket(Bucket=BUCKET)
    except ClientError as error:
        sys.exit(f"ERROR: cannot access bucket {BUCKET}: {error}\n"
                 "Run cloudstorage.py first.")

    written = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):        # skip folder placeholders
                continue
            item = attributes_for(key)
            table.put_item(Item=item)
            written += 1
            print(f"put_item: {item}")

    print(f"\nWrote {written} item(s) into {TABLE_NAME} at {DDB_ENDPOINT}")

    # Echo the table back so the contents are visible without switching tools
    scanned = table.scan()
    print(f"Scan returned {scanned['Count']} item(s):")
    for item in scanned["Items"]:
        print("  ", item)


if __name__ == "__main__":
    main()
