#!/usr/bin/env python3
"""
CITS5503 Lab 4 - AES encryption/decryption with pycryptodome.

For every original file in the S3 bucket:
  * download it,
  * encrypt it locally with AES-256-CBC, writing <file>.aes.enc into the same
    folder as the original,
  * decrypt that back into <file>.aes.dec in the same folder,
  * upload both to the bucket.

This is the script to open during Checkpoint 3. The two functions marked
CHECKPOINT 3 below are the encryption and decryption; main() is the loop that
applies them to each file in the bucket.

The AES key is generated once and kept at ~/cits5503/lab4/aes.key so that a
later run can still decrypt an earlier run's output. It never leaves the
machine - that is exactly the difference from the KMS approach, where the key
material stays inside AWS and every operation is an API call.

Usage:
    python3 fileencrypt.py              # encrypt, decrypt, upload
    python3 fileencrypt.py --no-upload  # local files only
"""

import os
import sys
import time


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
from Crypto.Random import get_random_bytes  # noqa: E402
from Crypto.Util.Padding import pad, unpad  # noqa: E402


LAB_DIR = os.path.expanduser("~/cits5503/lab4")
KEY_PATH = os.path.join(LAB_DIR, "aes.key")
KEY_BYTES = 32  # AES-256
DERIVED_SUFFIXES = (".aes.enc", ".aes.dec", ".kms.enc", ".kms.dec")


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

s3 = boto3.client("s3", region_name=REGION)


def get_aes_key():
    """Load the AES-256 key, generating and saving it on the first run."""
    os.makedirs(LAB_DIR, exist_ok=True)
    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, "rb") as handle:
            key = handle.read()
        if len(key) == KEY_BYTES:
            return key
        print(f"{KEY_PATH} is the wrong length - regenerating it")
    key = get_random_bytes(KEY_BYTES)
    with open(KEY_PATH, "wb") as handle:
        handle.write(key)
    os.chmod(KEY_PATH, 0o600)
    print(f"Generated a new AES-256 key at {KEY_PATH}")
    return key


# ===========================================================================
# CHECKPOINT 3 - this is the part that encrypts and decrypts each file
# ===========================================================================
def encrypt_file(key, in_path, out_path):
    """AES-256-CBC encrypt in_path to out_path.

    A fresh random IV is generated per file and written as the first 16 bytes
    of the output, so the same key can safely encrypt many files. The plaintext
    is PKCS#7 padded up to the 16-byte AES block size.
    """
    with open(in_path, "rb") as handle:
        plaintext = handle.read()

    cipher = AES.new(key, AES.MODE_CBC)          # random IV chosen for us
    ciphertext = cipher.encrypt(pad(plaintext, AES.block_size))

    with open(out_path, "wb") as handle:
        handle.write(cipher.iv + ciphertext)     # IV first, then ciphertext

    return len(plaintext), len(ciphertext) + len(cipher.iv)


def decrypt_file(key, in_path, out_path):
    """Reverse encrypt_file: read the IV back off the front, then decrypt."""
    with open(in_path, "rb") as handle:
        blob = handle.read()

    iv, ciphertext = blob[:AES.block_size], blob[AES.block_size:]
    cipher = AES.new(key, AES.MODE_CBC, iv)
    plaintext = unpad(cipher.decrypt(ciphertext), AES.block_size)

    with open(out_path, "wb") as handle:
        handle.write(plaintext)

    return len(plaintext)
# ===========================================================================


def original_keys():
    """Every object in the bucket that is not something this lab produced."""
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/") or key.endswith(DERIVED_SUFFIXES):
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

    key = get_aes_key()
    keys = original_keys()
    if not keys:
        sys.exit(f"No original objects found in {BUCKET}. Run ./lab4_prepare.sh")

    print(f"Bucket : {BUCKET}")
    print("Cipher : AES-256-CBC (pycryptodome)")
    print(f"Files  : {len(keys)}\n")

    started = time.perf_counter()

    for s3_key in keys:
        local = os.path.join(LAB_DIR, s3_key)
        os.makedirs(os.path.dirname(local), exist_ok=True)
        s3.download_file(BUCKET, s3_key, local)

        # Encrypted and decrypted files land in the same folder as the original.
        enc_path = local + ".aes.enc"
        dec_path = local + ".aes.dec"

        plain_len, cipher_len = encrypt_file(key, local, enc_path)
        decrypt_file(key, enc_path, dec_path)

        with open(local, "rb") as a, open(dec_path, "rb") as b:
            match = "round trip OK" if a.read() == b.read() else "MISMATCH"

        print(f"{s3_key}")
        print(f"  -> {os.path.relpath(enc_path, LAB_DIR)}  "
              f"({plain_len} B plaintext, {cipher_len} B ciphertext)")
        print(f"  -> {os.path.relpath(dec_path, LAB_DIR)}  ({match})")

        if upload:
            s3.upload_file(enc_path, BUCKET, s3_key + ".aes.enc")
            s3.upload_file(dec_path, BUCKET, s3_key + ".aes.dec")
            print(f"  uploaded {s3_key}.aes.enc and {s3_key}.aes.dec")

    elapsed = time.perf_counter() - started
    print(f"\nLocal AES over {len(keys)} file(s): {elapsed:.3f}s wall clock.")
    print("Compare that with kms_encrypt.py, where every file costs a round "
          "trip to the KMS API.")

    print("\nObjects now in the bucket:")
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET):
        for obj in page.get("Contents", []):
            print(f"  {obj['Key']}")


if __name__ == "__main__":
    main()
