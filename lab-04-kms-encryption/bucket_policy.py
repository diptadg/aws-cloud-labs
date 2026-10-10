#!/usr/bin/env python3
"""
CITS5503 Lab 4 - Apply a policy to restrict permissions on the bucket.

Applies a bucket policy to <prefix>-cloudstorage that DENIES s3:* to
every principal whose aws:username is not yours. You keep full access; everyone
else - including a facilitator signed in with their own AWS account - gets
AccessDenied.

Two deviations from the policy printed in the lab sheet, both deliberate:

  * Resource covers BOTH the bucket itself and the objects inside it
    (arn:aws:s3:::bucket and arn:aws:s3:::bucket/*). The sheet's example only
    lists an object path; with that alone, ListBucket on the bucket is not
    denied and a facilitator can still see the object listing.
  * The username in the Condition is read from your caller identity rather than
    hardcoded, so it can never drift out of sync with the account you are
    actually signed in as.

Usage:
    python3 bucket_policy.py            # apply the policy
    python3 bucket_policy.py --show     # print the policy currently attached
    python3 bucket_policy.py --remove   # take the policy off again
"""

import json
import os
import sys
from datetime import datetime


# ---------------------------------------------------------------------------
# Re-exec inside the shared venv if boto3 is not importable from this python.
# Ubuntu 26.04 blocks system-wide pip installs, so boto3 only lives in the venv.
# ---------------------------------------------------------------------------
def _ensure_venv():
    if os.environ.get("LAB4_REEXEC") == "1":
        return
    try:
        import boto3  # noqa: F401
        return
    except ImportError:
        pass
    home = os.path.expanduser("~")
    for cand in (
        os.environ.get("VIRTUAL_ENV", ""),
        os.path.join(home, "cits5503", "labenv"),
        os.path.join(home, "cits5503", ".venv"),
        os.path.join(home, "cits5503", "venv"),
        os.path.join(home, "labenv"),
        os.path.join(home, ".venv"),
        os.path.join(home, "venv"),
    ):
        if not cand:
            continue
        py = os.path.join(cand, "bin", "python3")
        if os.path.exists(py):
            os.environ["LAB4_REEXEC"] = "1"
            os.execv(py, [py] + sys.argv)
    sys.exit("ERROR: boto3 not available. Activate the venv first:  labenv")


_ensure_venv()

import boto3  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402


# ---------------------------------------------------------------------------
# Configuration, read from ~/cits5503/lab.env where possible
# ---------------------------------------------------------------------------
LAB_ENV_PATH = os.environ.get("LAB_ENV") or os.path.expanduser("~/cits5503/lab.env")
LAB_DIR = os.path.expanduser("~/cits5503/lab4")


def load_lab_env():
    """Read ~/cits5503/lab.env.

    Every configured value in this script comes from there. Nothing is
    hardcoded and nothing is guessed, so a stale or half-written lab.env fails
    loudly instead of quietly pointing at the wrong bucket.
    """
    if not os.path.exists(LAB_ENV_PATH):
        sys.exit(
            f"ERROR: {LAB_ENV_PATH} not found.\n"
            "Run ./lab4_prepare.sh first - it populates the Lab 4 values."
        )
    values = {}
    with open(LAB_ENV_PATH) as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def require(env, key):
    value = env.get(key, "").strip()
    if not value:
        sys.exit(
            f"ERROR: {key} is not set in {LAB_ENV_PATH}.\n"
            "Run ./lab4_prepare.sh to populate the Lab 4 values."
        )
    return value


ENV = load_lab_env()
LAB_PREFIX = require(ENV, "LAB_PREFIX")
REGION = require(ENV, "AWS_REGION")
BUCKET = require(ENV, "BUCKET_NAME")
IAM_USERNAME = require(ENV, "IAM_USERNAME")

s3 = boto3.client("s3", region_name=REGION)
sts = boto3.client("sts", region_name=REGION)


def caller_username():
    """Return IAM_USERNAME from lab.env, checked against the live credentials.

    The value used in the policy is the one recorded in lab.env. It is compared
    against sts:GetCallerIdentity so that a lab.env left over from a different
    account cannot produce a policy that locks you out of your own bucket.
    """
    arn = sts.get_caller_identity()["Arn"]
    if ":user/" not in arn:
        sys.exit(
            f"ERROR: current identity is {arn}\n"
            "This policy keys off aws:username, which only exists for IAM\n"
            "users. Sign in with your IAM user credentials before running it."
        )
    live_username = arn.split(":user/", 1)[1]
    if live_username != IAM_USERNAME:
        sys.exit(
            f"ERROR: lab.env says IAM_USERNAME={IAM_USERNAME}\n"
            f"       but the current credentials are {live_username}.\n"
            "Applying the policy now would deny you access to your own bucket.\n"
            "Re-run ./lab4_prepare.sh to refresh lab.env."
        )
    return IAM_USERNAME, arn


def build_policy(username):
    """The Lab 4 policy: deny s3:* to anyone who is not this IAM user."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowAllS3ActionsInUserFolderForUserOnly",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:*",
                # Both the bucket and its objects, so listing is denied too.
                "Resource": [
                    f"arn:aws:s3:::{BUCKET}",
                    f"arn:aws:s3:::{BUCKET}/*",
                ],
                "Condition": {
                    "StringNotLike": {
                        "aws:username": username,
                    }
                },
            }
        ],
    }


def current_policy():
    try:
        return s3.get_bucket_policy(Bucket=BUCKET)["Policy"]
    except ClientError as error:
        if error.response["Error"]["Code"] in (
            "NoSuchBucketPolicy",
            "NoSuchBucket",
        ):
            return None
        raise


def backup(policy_text):
    os.makedirs(LAB_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(LAB_DIR, f"bucket_policy_backup_{stamp}.json")
    with open(path, "w") as handle:
        handle.write(policy_text)
    print(f"Previous policy backed up to {path}")


def show():
    policy_text = current_policy()
    if policy_text is None:
        print(f"No bucket policy is attached to {BUCKET}.")
        return
    print(f"Policy currently attached to {BUCKET}:\n")
    print(json.dumps(json.loads(policy_text), indent=2))


def remove():
    policy_text = current_policy()
    if policy_text is None:
        print(f"No bucket policy attached to {BUCKET} - nothing to remove.")
        return
    backup(policy_text)
    s3.delete_bucket_policy(Bucket=BUCKET)
    print(f"Bucket policy removed from {BUCKET}.")


def apply():
    username, arn = caller_username()
    print(f"Signed in as : {arn}")
    print(f"aws:username : {username}")
    print(f"Bucket       : {BUCKET}\n")

    try:
        s3.head_bucket(Bucket=BUCKET)
    except ClientError as error:
        sys.exit(
            f"ERROR: cannot reach bucket {BUCKET}: {error}\n"
            "Run ./lab4_prepare.sh first."
        )

    existing = current_policy()
    if existing:
        backup(existing)

    policy = build_policy(username)
    print("Applying this policy:\n")
    print(json.dumps(policy, indent=2))

    s3.put_bucket_policy(Bucket=BUCKET, Policy=json.dumps(policy))
    print(f"\nPolicy applied to {BUCKET}.")
    print(
        "\nNote: this Deny also catches the account root user, because root has\n"
        "no aws:username. Keep using your IAM user for everything in this lab.\n"
        "If you ever need to undo it:  python3 bucket_policy.py --remove"
    )


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--show":
        show()
    elif arg == "--remove":
        remove()
    elif arg in ("", "--apply"):
        apply()
    else:
        sys.exit(f"Unknown option {arg!r}. Use --apply, --show or --remove.")
