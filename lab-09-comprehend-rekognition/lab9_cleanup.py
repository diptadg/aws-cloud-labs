#!/usr/bin/env python3
"""CITS5503 Lab 9 - delete the resources the lab created.

The lab sheet ends with "delete all AWS resources you have created". For Lab 9
that is exactly one thing: the S3 bucket holding the four images.

Every Comprehend and Rekognition call in this lab is synchronous and stateless -
detect_sentiment, detect_labels and the rest analyse the input and return, with
no job, endpoint, collection or model left behind - so there is nothing on those
two services to delete. (Asynchronous Comprehend jobs and Rekognition face
collections do persist, but this lab uses neither.)

Run with the shared venv active:
    labenv
    python3 lab9_cleanup.py

    python3 lab9_cleanup.py --empty   remove the objects, keep the bucket
    python3 lab9_cleanup.py -y        skip the confirmation prompt
"""

import argparse
import sys

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
except ImportError:
    sys.exit("boto3 not found. Activate the shared lab venv first:  labenv")

from lab9_env import load_env, require, report

cfg = load_env()
LAB_PREFIX, AWS_REGION = require(cfg, "LAB_PREFIX", "AWS_REGION")
BUCKET = require(cfg, "LAB9_BUCKET")

s3c = boto3.client("s3", region_name=AWS_REGION)
s3r = boto3.resource("s3", region_name=AWS_REGION)


def bucket_exists() -> bool:
    try:
        s3c.head_bucket(Bucket=BUCKET)
        return True
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("404", "NoSuchBucket"):
            return False
        if code == "403":
            sys.exit(f"{BUCKET} exists but belongs to another account - not touching it.")
        raise


def contents() -> list:
    keys = []
    paginator = s3c.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return keys


def empty_bucket() -> int:
    bucket = s3r.Bucket(BUCKET)
    keys = contents()
    bucket.objects.all().delete()
    # A versioned bucket keeps delete markers, and those block delete_bucket.
    try:
        bucket.object_versions.all().delete()
    except ClientError:
        pass
    return len(keys)


def main() -> None:
    ap = argparse.ArgumentParser(description="Delete the Lab 9 AWS resources.")
    ap.add_argument("--empty", action="store_true",
                    help="delete the objects but keep the bucket")
    ap.add_argument("-y", "--yes", action="store_true",
                    help="skip the confirmation prompt")
    args = ap.parse_args()

    report({
        "LAB_PREFIX": LAB_PREFIX,
        "AWS_REGION": AWS_REGION,
        "LAB9_BUCKET": BUCKET,
        "Action": "empty the bucket" if args.empty else "empty and delete the bucket",
    })

    try:
        if not bucket_exists():
            print(f"Bucket {BUCKET} does not exist - nothing to delete.")
            return
    except NoCredentialsError:
        sys.exit("No AWS credentials. Run 'aws configure' (Lab 1) first.")

    keys = contents()
    if keys:
        print(f"s3://{BUCKET}/ currently holds {len(keys)} object(s):")
        for key in keys:
            print(f"  {key}")
    else:
        print(f"s3://{BUCKET}/ is already empty.")
    print()

    if not args.yes:
        print("This is permanent. The images are the only thing in the bucket,")
        print("so re-running the lab means uploading them again.")
        if input("Type 'delete' to continue: ").strip() != "delete":
            sys.exit("Cancelled.")
        print()

    removed = empty_bucket()
    print(f"Removed {removed} object(s) from {BUCKET}.")

    if args.empty:
        print(f"Bucket {BUCKET} kept. Re-upload with:  python3 lab9_bucket.py")
        return

    s3c.delete_bucket(Bucket=BUCKET)
    print(f"Deleted bucket {BUCKET}.")
    print()
    print("Nothing else to remove: the Comprehend and Rekognition calls in this")
    print("lab create no persistent resources.")
    print("Confirm in the console: S3 -> Buckets.")


if __name__ == "__main__":
    main()
