#!/usr/bin/env python3
"""
CITS5503 Lab 4 - Use the created KMS key for encryption/decryption.

For every original file in the S3 bucket:
  * download it,
  * encrypt it with the KMS key aliased to your LAB_PREFIX, writing
    <file>.kms.enc into the same folder as the original,
  * decrypt that back into <file>.kms.dec in the same folder,
  * check the round trip matches the original byte for byte,
  * upload both new files to the bucket.

Note on the 4 KB limit: kms.encrypt only accepts up to 4096 bytes of
plaintext, which is fine for the tiny lab files. Anything larger is handled
with envelope encryption instead - KMS generates a data key, AES-256 does the
bulk work locally, and the encrypted data key travels with the ciphertext. A
one-byte header records which of the two was used so decryption knows what to
do.

Usage:
    python3 kms_encrypt.py             # encrypt, decrypt, upload
    python3 kms_encrypt.py --no-upload # local files only, skip the upload
"""

import os
import struct
import sys


def _ensure_venv():
    if os.environ.get("LAB4_REEXEC") == "1":
        return
    try:
        import boto3  # noqa: F401
        import Crypto  # noqa: F401
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
    sys.exit(
        "ERROR: boto3 / pycryptodome not available.\n"
        "Activate the venv with 'labenv', or re-run ./lab4_prepare.sh"
    )


_ensure_venv()

import boto3  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402
from Crypto.Cipher import AES  # noqa: E402
from Crypto.Util.Padding import pad, unpad  # noqa: E402


LAB_DIR = os.path.expanduser("~/cits5503/lab4")
KMS_MAX_PLAINTEXT = 4096
MODE_DIRECT = b"\x01"
MODE_ENVELOPE = b"\x02"

# Files produced by this lab, so re-runs do not encrypt their own output.
DERIVED_SUFFIXES = (".kms.enc", ".kms.dec", ".aes.enc", ".aes.dec")


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


ENV = load_lab_env()
LAB_PREFIX = require(ENV, "LAB_PREFIX")
REGION = require(ENV, "AWS_REGION")
BUCKET = require(ENV, "BUCKET_NAME")
ALIAS = require(ENV, "KMS_ALIAS")

s3 = boto3.client("s3", region_name=REGION)
kms = boto3.client("kms", region_name=REGION)


# ---------------------------------------------------------------------------
# Encryption / decryption with the KMS key
# ---------------------------------------------------------------------------
def kms_encrypt(plaintext):
    if len(plaintext) <= KMS_MAX_PLAINTEXT:
        blob = kms.encrypt(KeyId=ALIAS, Plaintext=plaintext)["CiphertextBlob"]
        return MODE_DIRECT + struct.pack(">I", len(blob)) + blob

    # Envelope encryption for anything over the KMS plaintext limit.
    data_key = kms.generate_data_key(KeyId=ALIAS, KeySpec="AES_256")
    cipher = AES.new(data_key["Plaintext"], AES.MODE_CBC)
    body = cipher.encrypt(pad(plaintext, AES.block_size))
    wrapped = data_key["CiphertextBlob"]
    return (
        MODE_ENVELOPE
        + struct.pack(">I", len(wrapped))
        + wrapped
        + cipher.iv
        + body
    )


def kms_decrypt(payload):
    mode, payload = payload[:1], payload[1:]
    blob_len = struct.unpack(">I", payload[:4])[0]
    blob, rest = payload[4:4 + blob_len], payload[4 + blob_len:]

    if mode == MODE_DIRECT:
        return kms.decrypt(CiphertextBlob=blob, KeyId=ALIAS)["Plaintext"]

    if mode == MODE_ENVELOPE:
        data_key = kms.decrypt(CiphertextBlob=blob, KeyId=ALIAS)["Plaintext"]
        iv, body = rest[:AES.block_size], rest[AES.block_size:]
        cipher = AES.new(data_key, AES.MODE_CBC, iv)
        return unpad(cipher.decrypt(body), AES.block_size)

    raise ValueError(f"Unrecognised header byte {mode!r} - not a .kms.enc file")


# ---------------------------------------------------------------------------
# Walking the bucket
# ---------------------------------------------------------------------------
def original_keys():
    """Every object in the bucket that is not something this lab produced."""
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue
            if key.endswith(DERIVED_SUFFIXES):
                continue
            keys.append(key)
    return sorted(keys)


def main():
    upload = "--no-upload" not in sys.argv

    try:
        s3.head_bucket(Bucket=BUCKET)
    except ClientError as error:
        sys.exit(f"ERROR: cannot reach bucket {BUCKET}: {error}\n"
                 "Run ./lab4_prepare.sh first.")

    try:
        key_meta = kms.describe_key(KeyId=ALIAS)["KeyMetadata"]
    except ClientError as error:
        sys.exit(f"ERROR: cannot resolve {ALIAS}: {error}\n"
                 "Run: python3 kms_key.py")

    print(f"Bucket : {BUCKET}")
    print(f"KMS key: {ALIAS} -> {key_meta['KeyId']}\n")

    keys = original_keys()
    if not keys:
        sys.exit(f"No original objects found in {BUCKET}. Run ./lab4_prepare.sh")

    for key in keys:
        local = os.path.join(LAB_DIR, key)
        os.makedirs(os.path.dirname(local), exist_ok=True)

        s3.download_file(BUCKET, key, local)
        with open(local, "rb") as handle:
            plaintext = handle.read()

        # Encrypted and decrypted files land beside the original.
        enc_path = local + ".kms.enc"
        dec_path = local + ".kms.dec"

        ciphertext = kms_encrypt(plaintext)
        with open(enc_path, "wb") as handle:
            handle.write(ciphertext)

        recovered = kms_decrypt(ciphertext)
        with open(dec_path, "wb") as handle:
            handle.write(recovered)

        match = "round trip OK" if recovered == plaintext else "MISMATCH"
        print(f"{key}")
        print(f"  -> {os.path.relpath(enc_path, LAB_DIR)}  "
              f"({len(plaintext)} B plaintext, {len(ciphertext)} B ciphertext)")
        print(f"  -> {os.path.relpath(dec_path, LAB_DIR)}  ({match})")

        if upload:
            s3.upload_file(enc_path, BUCKET, key + ".kms.enc")
            s3.upload_file(dec_path, BUCKET, key + ".kms.dec")
            print(f"  uploaded {key}.kms.enc and {key}.kms.dec")

    print("\nObjects now in the bucket:")
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET):
        for obj in page.get("Contents", []):
            print(f"  {obj['Key']}")


if __name__ == "__main__":
    main()
