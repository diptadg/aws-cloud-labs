#!/usr/bin/env python3
"""CITS5503 Lab 8 - produce a copy of LabAI.ipynb with your values already in it.

The notebook baked into the image is the unedited one from the unit repository:
it has YOUR_STUDENTID and YOUR_BUCKET_NAME placeholders that you would otherwise
retype inside Jupyter every time the task restarts. This writes a filled copy
next to the scripts, which you upload once through the Jupyter file browser.

The source defaults to LAB8_NOTEBOOK_URL, the same URL the Dockerfile fetches.
Pass --source with a local path to fill in the copy attached to the lab sheet
instead; the two differ only in markdown, but use whichever your unit hands out.

Five edits are made:

  * student_id and bucket are filled from lab.env
  * the SageMaker role ARN is built from the account STS reports, instead of the
    account number the unit repository hard-codes
  * the credentials cell tries the ECS task role first, and only prompts for
    access keys if that fails
  * the install cell pins the SageMaker SDK to LAB8_SAGEMAKER_SPEC, so it
    cannot pull v3 over the top of the v2 the image was built with
  * the image_uris import works on either SDK major version: v3 moved it from
    sagemaker.image_uris to sagemaker.core.image_uris

Run with the shared venv active:
    labenv
    python3 lab8_notebook.py

    python3 lab8_notebook.py --source ~/Downloads/LabAI.ipynb
    python3 lab8_notebook.py --out /some/path.ipynb
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from lab8_env import load_env, require, optional, report

cfg = load_env()
LAB_PREFIX, AWS_REGION = require(cfg, "LAB_PREFIX", "AWS_REGION")
BUCKET, NOTEBOOK, NOTEBOOK_URL, TASK_ROLE = require(
    cfg, "LAB8_BUCKET", "LAB8_NOTEBOOK", "LAB8_NOTEBOOK_URL", "LAB8_TASK_ROLE")
SAGEMAKER_SPEC = optional(cfg, "LAB8_SAGEMAKER_SPEC", "sagemaker>=2.200,<3")

CREDENTIALS_CELL = f"""\
# The ECS task runs under the {TASK_ROLE} task role, so boto3 can usually pick
# credentials up from the container metadata endpoint with no input at all.
#
# Typing a secret key below sends it over plain HTTP to a public IP address, so
# prefer the task role, and delete the ECS service as soon as the lab is done.
import os
import boto3
from getpass import getpass

os.environ.setdefault('AWS_DEFAULT_REGION', '{AWS_REGION}')

try:
    boto3.client('sts').get_caller_identity()
    print('Using the credentials already available in the container.')
except Exception:
    print('No usable credentials found. Enter them for this session only.')
    os.environ['AWS_ACCESS_KEY_ID'] = input('AWS_ACCESS_KEY_ID: ')
    os.environ['AWS_SECRET_ACCESS_KEY'] = getpass('AWS_SECRET_ACCESS_KEY (hidden): ')
    os.environ['AWS_DEFAULT_REGION'] = input('AWS_DEFAULT_REGION [{AWS_REGION}]: ') or '{AWS_REGION}'
"""


INSTALL_CELL = f"""\
# Install SageMaker, pandas and numpy.
# The SDK is pinned below v3: v3 moved image_uris and depends on torch plus the
# NVIDIA CUDA libraries, several gigabytes this CPU-only container cannot use.
!pip3 install "{SAGEMAKER_SPEC}" pandas numpy
"""

RETRIEVE_IMPORT = """\
try:
    from sagemaker.image_uris import retrieve        # SageMaker SDK v2
except ModuleNotFoundError:
    from sagemaker.core.image_uris import retrieve   # SageMaker SDK v3 moved it
"""


def source(cell) -> str:
    src = cell.get("source", "")
    return src if isinstance(src, str) else "".join(src)


def main() -> None:
    ap = argparse.ArgumentParser(description="Fill in the Lab 8 notebook.")
    ap.add_argument("--source", default=NOTEBOOK_URL,
                    help="local .ipynb path or URL to fill in "
                         "(default: LAB8_NOTEBOOK_URL from lab.env)")
    ap.add_argument("--out", default=None, help="output path")
    args = ap.parse_args()

    out = Path(args.out) if args.out else Path(__file__).resolve().parent / \
        f"{Path(NOTEBOOK).stem}_{LAB_PREFIX}.ipynb"

    report({
        "LAB_PREFIX": LAB_PREFIX,
        "AWS_REGION": AWS_REGION,
        "LAB8_BUCKET": BUCKET,
        "Source": args.source,
        "Output": str(out),
    })

    # The unit hands the notebook out in two places: the raw GitHub URL the
    # Dockerfile wgets, and a file attached to the lab sheet. They differ in a
    # couple of markdown cells, so accept either.
    if "://" in args.source:
        try:
            with urllib.request.urlopen(args.source, timeout=30) as resp:
                nb = json.loads(resp.read().decode())
        except Exception as exc:
            sys.exit(f"Could not download {args.source}: {exc}")
    else:
        path = Path(args.source).expanduser()
        if not path.is_file():
            sys.exit(f"{path} not found.")
        try:
            nb = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            sys.exit(f"{path} is not a valid notebook: {exc}")

    filled_id = filled_creds = filled_pip = filled_import = 0
    cells = []
    for cell in nb["cells"]:
        text = source(cell)

        if cell["cell_type"] == "code" and "AWS_ACCESS_KEY_ID" in text and "getpass" in text:
            cell["source"] = CREDENTIALS_CELL.splitlines(keepends=True)
            filled_creds += 1

        elif cell["cell_type"] == "code" and "pip3 install sagemaker" in text:
            cell["source"] = INSTALL_CELL.splitlines(keepends=True)
            filled_pip += 1

        elif cell["cell_type"] == "code" and "from sagemaker.image_uris import retrieve" in text:
            text = text.replace("from sagemaker.image_uris import retrieve\n", RETRIEVE_IMPORT, 1)
            cell["source"] = text.splitlines(keepends=True)
            filled_import += 1

        elif "YOUR_STUDENTID" in text or "YOUR_BUCKET_NAME" in text:
            text = text.replace(
                'student_id = "YOUR_STUDENTID"  # use your student id',
                f'student_id = "{LAB_PREFIX}"',
            ).replace(
                'bucket = "YOUR_BUCKET_NAME"  # use <studentid-lab8> as your bucket name',
                f'bucket = "{BUCKET}"  # created by lab8_bucket.py',
            )
            # The unit repository pins the role to one account number. Ask STS
            # instead, so the notebook is correct whoever runs it.
            for line in text.splitlines():
                if line.startswith("sagemaker_role = "):
                    text = text.replace(
                        line,
                        'account_id = boto3.client("sts").get_caller_identity()["Account"]\n'
                        f'sagemaker_role = f"arn:aws:iam::{{account_id}}:role/{TASK_ROLE}"',
                    )
            cell["source"] = text.splitlines(keepends=True)
            filled_id += 1

        # Empty code cells confuse "run all"; the source notebook has two.
        if cell["cell_type"] == "code" and not source(cell).strip():
            continue
        cells.append(cell)

    nb["cells"] = cells

    if not filled_id:
        print("Warning: no YOUR_STUDENTID placeholder found - has the source notebook changed?")
    if not filled_creds:
        print("Warning: no credentials cell found - has the source notebook changed?")
    if not filled_pip:
        print("Warning: no pip install cell found - has the source notebook changed?")
    if not filled_import:
        print("Warning: no image_uris import found - has the source notebook changed?")

    out.write_text(json.dumps(nb, indent=1))
    print(f"Wrote {out}")
    print("\nUpload it from the Jupyter file browser (the Upload button, top right),")
    print("then run the cells in order.")


if __name__ == "__main__":
    main()
