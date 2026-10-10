#!/usr/bin/env python3
"""
CITS5503 Lab 4 - Create a KMS key and attach a key policy.

  [1] Creates a symmetric KMS key with your LAB_PREFIX as its alias.
  [2] Attaches the key policy from the lab sheet, with <your_username>
      replaced by the IAM user behind your current credentials, so the policy
      names you as both key administrator and key user.

Idempotent: if alias/<prefix> already resolves to a key, that key is
reused and only the policy is re-applied. Safe to run repeatedly.

Usage:
    python3 kms_key.py            # create/reuse the key and attach the policy
    python3 kms_key.py --show     # print the policy currently on the key
"""

import json
import os
import re
import sys


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


LAB_ENV_PATH = os.environ.get("LAB_ENV") or os.path.expanduser("~/cits5503/lab.env")


def load_lab_env():
    """Read ~/cits5503/lab.env.

    Every configured value in this script comes from there - nothing is
    hardcoded and nothing is guessed.
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


def set_lab_env(key, value):
    """Write key="value" into ~/cits5503/lab.env, replacing any existing line."""
    os.makedirs(os.path.dirname(LAB_ENV_PATH), exist_ok=True)
    lines = []
    if os.path.exists(LAB_ENV_PATH):
        with open(LAB_ENV_PATH) as handle:
            lines = handle.read().splitlines()
    pattern = re.compile(rf"^{re.escape(key)}=")
    replaced = False
    for index, line in enumerate(lines):
        if pattern.match(line):
            lines[index] = f'{key}="{value}"'
            replaced = True
            break
    if not replaced:
        lines.append(f'{key}="{value}"')
    with open(LAB_ENV_PATH, "w") as handle:
        handle.write("\n".join(lines) + "\n")


ENV = load_lab_env()
LAB_PREFIX = require(ENV, "LAB_PREFIX")
REGION = require(ENV, "AWS_REGION")
ALIAS = require(ENV, "KMS_ALIAS")
IAM_USERNAME = require(ENV, "IAM_USERNAME")
ACCOUNT_ID = require(ENV, "ACCOUNT_ID")

kms = boto3.client("kms", region_name=REGION)
sts = boto3.client("sts", region_name=REGION)


def identity():
    """Return the account and IAM user from lab.env, checked against STS.

    The key policy is built from the lab.env values; the live identity is only
    used to confirm they still match, so a stale lab.env cannot produce a key
    policy that names somebody else as administrator.
    """
    caller = sts.get_caller_identity()
    arn = caller["Arn"]
    if ":user/" not in arn:
        sys.exit(
            f"ERROR: current identity is {arn}\n"
            "The key policy has to name an IAM user ARN. Sign in with your\n"
            "IAM user credentials before running this."
        )
    live_username = arn.split(":user/", 1)[1]
    if live_username != IAM_USERNAME or caller["Account"] != ACCOUNT_ID:
        sys.exit(
            f"ERROR: lab.env says {ACCOUNT_ID} / {IAM_USERNAME}\n"
            f"       but the current credentials are {caller['Account']} / {live_username}.\n"
            "Re-run ./lab4_prepare.sh to refresh lab.env."
        )
    return ACCOUNT_ID, IAM_USERNAME, arn


def build_key_policy(account_id, username):
    """The key policy from the lab sheet, with your IAM user filled in."""
    user_arn = f"arn:aws:iam::{account_id}:user/{username}"
    return {
        "Version": "2012-10-17",
        "Id": "key-consolepolicy-3",
        "Statement": [
            {
                "Sid": "Enable IAM User Permissions",
                "Effect": "Allow",
                "Principal": {"AWS": f"arn:aws:iam::{account_id}:root"},
                "Action": "kms:*",
                "Resource": "*",
            },
            {
                # This statement is what makes you a Key Administrator in the
                # KMS console.
                "Sid": "Allow access for Key Administrators",
                "Effect": "Allow",
                "Principal": {"AWS": user_arn},
                "Action": [
                    "kms:Create*",
                    "kms:Describe*",
                    "kms:Enable*",
                    "kms:List*",
                    "kms:Put*",
                    "kms:Update*",
                    "kms:Revoke*",
                    "kms:Disable*",
                    "kms:Get*",
                    "kms:Delete*",
                    "kms:TagResource",
                    "kms:UntagResource",
                    "kms:ScheduleKeyDeletion",
                    "kms:CancelKeyDeletion",
                ],
                "Resource": "*",
            },
            {
                # And this one makes you a Key User.
                "Sid": "Allow use of the key",
                "Effect": "Allow",
                "Principal": {"AWS": user_arn},
                "Action": [
                    "kms:Encrypt",
                    "kms:Decrypt",
                    "kms:ReEncrypt*",
                    "kms:GenerateDataKey*",
                    "kms:DescribeKey",
                ],
                "Resource": "*",
            },
            {
                "Sid": "Allow attachment of persistent resources",
                "Effect": "Allow",
                "Principal": {"AWS": user_arn},
                "Action": ["kms:CreateGrant", "kms:ListGrants", "kms:RevokeGrant"],
                "Resource": "*",
                "Condition": {"Bool": {"kms:GrantIsForAWSResource": "true"}},
            },
        ],
    }


def find_key_by_alias():
    """Return the key id behind the alias, or None."""
    paginator = kms.get_paginator("list_aliases")
    for page in paginator.paginate():
        for entry in page["Aliases"]:
            if entry["AliasName"] == ALIAS and entry.get("TargetKeyId"):
                return entry["TargetKeyId"]
    return None


def show():
    key_id = find_key_by_alias()
    if not key_id:
        print(f"No key found behind {ALIAS}. Run: python3 kms_key.py")
        return
    policy = kms.get_key_policy(KeyId=key_id, PolicyName="default")["Policy"]
    print(f"Key      : {key_id}")
    print(f"Alias    : {ALIAS}\n")
    print(json.dumps(json.loads(policy), indent=2))


def main():
    account_id, username, arn = identity()
    print(f"Signed in as : {arn}")
    print(f"Region       : {REGION}")
    print(f"Alias        : {ALIAS}\n")

    key_id = find_key_by_alias()

    if key_id:
        print(f"Alias {ALIAS} already points at key {key_id} - reusing it.")
    else:
        # [1] Create the key
        print("Creating a symmetric KMS key ...")
        response = kms.create_key(
            Description=f"CITS5503 Lab 4 key for {LAB_PREFIX}",
            KeyUsage="ENCRYPT_DECRYPT",
            KeySpec="SYMMETRIC_DEFAULT",
            Origin="AWS_KMS",
            Tags=[{"TagKey": "Name", "TagValue": LAB_PREFIX}],
        )
        key_id = response["KeyMetadata"]["KeyId"]
        print(f"Key created: {key_id}")

        # The prefix works as the alias for the key.
        try:
            kms.create_alias(AliasName=ALIAS, TargetKeyId=key_id)
            print(f"Alias {ALIAS} created")
        except ClientError as error:
            if error.response["Error"]["Code"] == "AlreadyExistsException":
                kms.update_alias(AliasName=ALIAS, TargetKeyId=key_id)
                print(f"Alias {ALIAS} repointed at {key_id}")
            else:
                raise

    # [2] Attach the key policy
    policy = build_key_policy(account_id, username)
    print("\nAttaching this key policy:\n")
    print(json.dumps(policy, indent=2))
    kms.put_key_policy(
        KeyId=key_id,
        PolicyName="default",
        Policy=json.dumps(policy),
    )
    print("\nKey policy attached.")

    key_arn = kms.describe_key(KeyId=key_id)["KeyMetadata"]["Arn"]
    set_lab_env("KMS_KEY_ID", key_id)
    set_lab_env("KMS_KEY_ARN", key_arn)
    set_lab_env("KMS_ALIAS", ALIAS)

    print(f"\nKey id  : {key_id}")
    print(f"Key ARN : {key_arn}")
    print("Recorded in ~/cits5503/lab.env")
    print(
        "\nCheckpoint 2 is done in the console: KMS, Customer managed keys,\n"
        f"select the key with alias {ALIAS.split('/', 1)[1]}, open the Key policy tab and\n"
        f"show arn:aws:iam::{account_id}:user/{username} listed as both key\n"
        "administrator and key user."
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--show":
        show()
    else:
        main()
