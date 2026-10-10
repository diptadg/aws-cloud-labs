#!/usr/bin/env python3
# ------------------------------
# CITS5503 - Lab 3
#
# cloudstorage.py
#
# Copies local files to S3. Given a root local directory, walks every level
# and copies each file to the same path inside the bucket, so the bucket ends
# up with:
#
#   rootdir/
#   ├── rootfile.txt
#   └── subdir/
#       └── subfile.txt
#
# Run with the venv active:  labenv && python3 cloudstorage.py
# ------------------------------

import os
import sys

import boto3
from botocore.exceptions import ClientError


# ------------------------------------------------------------------
# Config - read from ~/cits5503/lab.env, which the Lab 1 setup script writes
# ------------------------------------------------------------------
LAB_ENV = os.environ.get("LAB_ENV") or os.path.expanduser("~/cits5503/lab.env")


def load_lab_env(path=LAB_ENV, required=("LAB_PREFIX", "AWS_REGION")):
    """Read lab.env, which is written by the Lab 1 setup script.

    That file is the single source of truth for the name prefix and region,
    so nothing is defaulted or duplicated here - if a value is missing, say so
    and stop rather than guessing.
    """
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
        sys.exit(
            f"ERROR: {', '.join(missing)} not set in {path}.\n"
            "Add the value(s) there (Lab 1 owns that file) and re-run."
        )
    return values


_env = load_lab_env()
LAB_PREFIX = _env["LAB_PREFIX"]
REGION = _env["AWS_REGION"]

ROOT_DIR = "rootdir"
# Written into lab.env by lab3_prepare.sh; derived if that hasn't run yet.
ROOT_S3_DIR = _env.get("BUCKET_NAME") or f"{LAB_PREFIX}-cloudstorage"

s3 = boto3.client("s3", region_name=REGION)

# us-east-1 is the historical exception: it must NOT be given a
# LocationConstraint. Every other region requires one.
bucket_config = {"LocationConstraint": REGION}


# ------------------------------------------------------------------
# Bucket creation (idempotent)
# ------------------------------------------------------------------
def create_bucket():
    try:
        if REGION == "us-east-1":
            response = s3.create_bucket(Bucket=ROOT_S3_DIR)
        else:
            response = s3.create_bucket(
                Bucket=ROOT_S3_DIR,
                CreateBucketConfiguration=bucket_config,
            )
        print(response)
        print(f"Created bucket {ROOT_S3_DIR} in {REGION}")
    except ClientError as error:
        code = error.response["Error"]["Code"]
        if code in ("BucketAlreadyOwnedByYou", "OperationAborted"):
            print(f"Bucket {ROOT_S3_DIR} already exists and is owned by you - reusing it")
        elif code == "BucketAlreadyExists":
            sys.exit(
                f"ERROR: the bucket name {ROOT_S3_DIR} is taken by another AWS "
                "account (S3 names are globally unique). Pick a different name."
            )
        else:
            raise


# ------------------------------------------------------------------
# Upload one file. folder_name is the S3 "directory" prefix, e.g. 'rootdir/'
# ------------------------------------------------------------------
def upload_file(folder_name, file, file_name):
    key = f"{folder_name}{file_name}"
    print("Uploading %s  ->  s3://%s/%s" % (file, ROOT_S3_DIR, key))
    s3.upload_file(file, ROOT_S3_DIR, key)


# ------------------------------------------------------------------
# Main program
# ------------------------------------------------------------------
if not os.path.isdir(ROOT_DIR):
    sys.exit(
        f"ERROR: '{ROOT_DIR}' not found in {os.getcwd()}.\n"
        "Run ./lab3_prepare.sh first, and run this script from ~/cits5503/lab3"
    )

create_bucket()

# Parse directory and upload files.
#
# CHECKPOINT 1 - this is the traversal to show the facilitator. os.walk visits
# rootdir and every subdirectory beneath it; the S3 key is built relative to
# rootdir's PARENT, so the 'rootdir/' prefix is preserved in the bucket.
parent_of_root = os.path.dirname(os.path.abspath(ROOT_DIR))

for dir_name, subdir_list, file_list in os.walk(ROOT_DIR, topdown=True):
    rel_dir = os.path.relpath(dir_name, parent_of_root)      # rootdir, rootdir/subdir
    folder_name = rel_dir.replace(os.sep, "/") + "/"          # rootdir/, rootdir/subdir/
    for fname in file_list:
        upload_file(folder_name, os.path.join(dir_name, fname), fname)

# Show what actually landed in the bucket
print("\nObjects now in s3://%s :" % ROOT_S3_DIR)
paginator = s3.get_paginator("list_objects_v2")
for page in paginator.paginate(Bucket=ROOT_S3_DIR):
    for obj in page.get("Contents", []):
        print("  %s  (%d bytes)" % (obj["Key"], obj["Size"]))

print("done")
