#!/usr/bin/env python3
"""CITS5503 Lab 8 - create or check the ECR repository and get a Docker login.

Two jobs, both from the lab sheet:

  1. create_or_check_repository() - idempotent: describe first, create only if
     ECR raises RepositoryNotFoundException. Records the repository URI in
     lab.env so nothing downstream has to paste it.

  2. get_docker_login_cmd() - decodes the ECR authorisation token into the
     username and password and builds the `docker login` command.

Run with the shared venv active:
    labenv
    python3 lab8_ecr.py                 create/check the repo, show the login command
    python3 lab8_ecr.py --login-cmd     print only the login command
    python3 lab8_ecr.py --password      print only the password (for --password-stdin)
    python3 lab8_ecr.py --delete        delete the repository and its images
"""

import argparse
import base64
import sys

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
except ImportError:
    sys.exit("boto3 not found. Activate the shared lab venv first:  labenv")

from lab8_env import load_env, require, update_env, report

cfg = load_env()
LAB_PREFIX, AWS_REGION = require(cfg, "LAB_PREFIX", "AWS_REGION")
REPO_NAME, IMAGE_TAG = require(cfg, "LAB8_ECR_REPO", "LAB8_IMAGE_TAG")

ecr = boto3.client("ecr", region_name=AWS_REGION)


def create_or_check_repository(repository_name: str) -> str:
    """Return the repository URI, creating the repository if it is not there."""
    try:
        response = ecr.describe_repositories(repositoryNames=[repository_name])
        uri = response["repositories"][0]["repositoryUri"]
        print(f"Repository {repository_name} already exists.")
    except ecr.exceptions.RepositoryNotFoundException:
        response = ecr.create_repository(repositoryName=repository_name)
        uri = response["repository"]["repositoryUri"]
        print(f"Created repository {repository_name}.")
    return uri


def auth() -> tuple:
    """Decode the ECR authorisation token into (username, password, registry)."""
    token = ecr.get_authorization_token()["authorizationData"][0]
    # split(":", 1), not split(":"): the token decodes to "AWS:<password>", and
    # splitting on every colon breaks if the password ever contains one.
    username, password = base64.b64decode(token["authorizationToken"]).decode().split(":", 1)
    return username, password, token["proxyEndpoint"]


def get_docker_login_cmd() -> str:
    username, password, registry = auth()
    return f"docker login -u {username} -p {password} {registry}"


def delete_repository(repository_name: str) -> None:
    try:
        ecr.delete_repository(repositoryName=repository_name, force=True)
        print(f"Deleted repository {repository_name} and every image in it.")
    except ecr.exceptions.RepositoryNotFoundException:
        print(f"Repository {repository_name} does not exist - nothing to delete.")
    update_env({"LAB8_ECR_URI": "", "LAB8_ECR_REGISTRY": ""})
    print("Cleared LAB8_ECR_URI and LAB8_ECR_REGISTRY in lab.env.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Lab 8 ECR repository and login.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--login-cmd", action="store_true",
                   help="print only the docker login command")
    g.add_argument("--password", action="store_true",
                   help="print only the password, for docker login --password-stdin")
    g.add_argument("--delete", action="store_true",
                   help="delete the repository and its images")
    args = ap.parse_args()

    # These two write nothing and print nothing else, so they can be piped.
    try:
        if args.password:
            print(auth()[1])
            return
        if args.login_cmd:
            print(get_docker_login_cmd())
            return
    except (ClientError, NoCredentialsError) as exc:
        sys.exit(f"Could not get an ECR token: {exc}")

    report({
        "LAB_PREFIX": LAB_PREFIX,
        "AWS_REGION": AWS_REGION,
        "LAB8_ECR_REPO": REPO_NAME,
        "LAB8_IMAGE_TAG": IMAGE_TAG,
    })

    if args.delete:
        delete_repository(REPO_NAME)
        return

    uri = create_or_check_repository(REPO_NAME)
    _, _, registry = auth()

    update_env({
        "LAB8_ECR_URI": uri,
        "LAB8_ECR_REGISTRY": registry,
    })

    print(f"\nECR URI: {uri}")
    print(f"Registry: {registry}")
    print("\nRecorded LAB8_ECR_URI and LAB8_ECR_REGISTRY in lab.env.")
    print("\nThe login command the lab sheet asks you to run is:\n")
    print(f"  {get_docker_login_cmd()}\n")
    print("The token is valid for 12 hours. ./lab8_push.sh runs the equivalent")
    print("login for you over stdin, so the password never lands in your shell")
    print("history or in the process list.")
    print(f"\nNext:  ./lab8_push.sh")


if __name__ == "__main__":
    main()
