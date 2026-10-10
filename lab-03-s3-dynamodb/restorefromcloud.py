#!/usr/bin/env python3
# ------------------------------
# CITS5503 - Lab 3
#
# restorefromcloud.py
#
# Reads the S3 bucket and writes the contents back out into the matching
# local directories, recreating the directory tree as it goes.
#
# Usage:
#   labenv && python3 restorefromcloud.py            # restores into ./restored/
#   python3 restorefromcloud.py --dest .             # restores over ./rootdir/
# ------------------------------

import argparse
import os
import sys

import boto3
from botocore.exceptions import ClientError


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
ROOT_S3_DIR = _env.get("BUCKET_NAME") or f"{LAB_PREFIX}-cloudstorage"

parser = argparse.ArgumentParser(description="Restore an S3 bucket to the local filesystem")
parser.add_argument("--bucket", default=ROOT_S3_DIR, help="bucket to restore from")
parser.add_argument(
    "--dest",
    default="restored",
    help="local directory to restore into (default: restored/). "
         "Use '.' to write straight back over rootdir/",
)
args = parser.parse_args()

s3 = boto3.client("s3", region_name=REGION)

try:
    s3.head_bucket(Bucket=args.bucket)
except ClientError as error:
    sys.exit(f"ERROR: cannot access bucket {args.bucket}: {error}")

dest_root = os.path.abspath(args.dest)
os.makedirs(dest_root, exist_ok=True)

count = 0
paginator = s3.get_paginator("list_objects_v2")
for page in paginator.paginate(Bucket=args.bucket):
    for obj in page.get("Contents", []):
        key = obj["Key"]

        # Keys ending in '/' are folder placeholders - just make the directory.
        if key.endswith("/"):
            os.makedirs(os.path.join(dest_root, key), exist_ok=True)
            continue

        local_path = os.path.join(dest_root, *key.split("/"))
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        print("Downloading s3://%s/%s  ->  %s" % (args.bucket, key, local_path))
        s3.download_file(args.bucket, key, local_path)
        count += 1

print("\nRestored %d file(s) under %s" % (count, dest_root))

# Show the restored tree so it is obvious the structure survived the round trip
for dir_name, _subdirs, files in os.walk(dest_root):
    depth = dir_name.replace(dest_root, "").count(os.sep)
    print("%s%s/" % ("  " * depth, os.path.basename(dir_name) or dest_root))
    for f in sorted(files):
        print("%s%s" % ("  " * (depth + 1), f))

print("done")
