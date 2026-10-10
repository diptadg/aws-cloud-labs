#!/usr/bin/env python3
"""CITS5503 Lab 8 - create the S3 bucket the tuning job reads and writes.

The notebook expects <prefix>-lab8 to already exist: its first cell only
does put_object on the prefix, which fails with NoSuchBucket if the bucket has
not been created. This script creates it, idempotently, and creates the
sagemaker/<prefix>-hpo-xgboost-dm prefix inside it.

Run with the shared venv active:
    labenv
    python3 lab8_bucket.py

    python3 lab8_bucket.py --list      show what the tuning job has written
"""

import argparse
import sys

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    sys.exit("boto3 not found. Activate the shared lab venv first:  labenv")

from lab8_env import load_env, require, report

cfg = load_env()
LAB_PREFIX, AWS_REGION = require(cfg, "LAB_PREFIX", "AWS_REGION")
BUCKET, PREFIX = require(cfg, "LAB8_BUCKET", "LAB8_S3_PREFIX")

s3 = boto3.client("s3", region_name=AWS_REGION)


def bucket_exists() -> bool:
    try:
        s3.head_bucket(Bucket=BUCKET)
        return True
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("404", "NoSuchBucket"):
            return False
        if code == "403":
            sys.exit(
                f"{BUCKET} exists but is owned by another account.\n"
                "S3 bucket names are globally unique. Pick a different value for\n"
                "LAB8_BUCKET in lab.env and re-run."
            )
        raise


def ensure_bucket() -> None:
    if bucket_exists():
        print(f"Bucket {BUCKET} already exists.")
        return

    params = {"Bucket": BUCKET}
    # us-east-1 is the one region that rejects an explicit LocationConstraint.
    if AWS_REGION != "us-east-1":
        params["CreateBucketConfiguration"] = {"LocationConstraint": AWS_REGION}

    try:
        s3.create_bucket(**params)
        print(f"Created bucket {BUCKET} in {AWS_REGION}.")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("BucketAlreadyOwnedByYou",):
            print(f"Bucket {BUCKET} already exists.")
        else:
            raise


def ensure_prefix() -> None:
    key = f"{PREFIX}/"
    s3.put_object(Bucket=BUCKET, Key=key)
    print(f"Prefix {key} ready.")


def listing() -> None:
    paginator = s3.get_paginator("list_objects_v2")
    total = 0
    for page in paginator.paginate(Bucket=BUCKET, Prefix=PREFIX):
        for obj in page.get("Contents", []):
            print(f"  {obj['Size']:>12,}  {obj['LastModified']:%Y-%m-%d %H:%M}  {obj['Key']}")
            total += 1
    if total == 0:
        print("  (empty - the notebook has not uploaded train.csv yet)")
    else:
        print(f"\n  {total} object(s) under s3://{BUCKET}/{PREFIX}/")


def main() -> None:
    ap = argparse.ArgumentParser(description="Lab 8 S3 bucket for the tuning job.")
    ap.add_argument("--list", action="store_true",
                    help="list what is under the prefix instead of creating it")
    args = ap.parse_args()

    report({
        "LAB_PREFIX": LAB_PREFIX,
        "AWS_REGION": AWS_REGION,
        "LAB8_BUCKET": BUCKET,
        "LAB8_S3_PREFIX": PREFIX,
    })

    if args.list:
        if not bucket_exists():
            sys.exit(f"Bucket {BUCKET} does not exist. Run this script without --list first.")
        listing()
        return

    ensure_bucket()
    ensure_prefix()
    print(f"\nThe notebook's bucket and prefix values are:")
    print(f'  bucket = "{BUCKET}"')
    print(f'  student_id = "{LAB_PREFIX}"')
    print(f"\nTuning job output will land under s3://{BUCKET}/{PREFIX}/output/")


if __name__ == "__main__":
    main()
