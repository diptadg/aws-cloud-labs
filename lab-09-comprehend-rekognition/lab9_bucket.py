#!/usr/bin/env python3
"""CITS5503 Lab 9 - create the S3 bucket and add the four images.

The lab sheet asks for a bucket named <prefix>-lab9 in your mapped
region, holding urban.jpg, beach.jpg, faces.jpg and text.jpg. Save the four
images from the lab sheet into ./images/ first; this script validates them
against what Rekognition will actually accept, then uploads them under the
required key names.

Run with the shared venv active:
    labenv
    python3 lab9_bucket.py

    python3 lab9_bucket.py --check    validate the local files, upload nothing
    python3 lab9_bucket.py --list     list what is in the bucket
    python3 lab9_bucket.py --force    re-upload even if the key already exists

Source files may be .jpg, .jpeg or .png: the four figures on the lab sheet are
a mix of the two. Whatever the source extension, the object is stored under the
name the lab sheet specifies, and Rekognition decides the format from the bytes
rather than the key, so a PNG stored as text.jpg still analyses correctly. The
Content-Type recorded on the object is the real one.
"""

import argparse
import sys
from pathlib import Path

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
except ImportError:
    sys.exit("boto3 not found. Activate the shared lab venv first:  labenv")

from lab9_env import load_env, require, optional, image_keys, report

# Rekognition's documented limits for an image passed by S3 reference.
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MIN_DIMENSION = 80
ACCEPTED_SUFFIXES = (".jpg", ".jpeg", ".png")

cfg = load_env()
LAB_PREFIX, AWS_REGION = require(cfg, "LAB_PREFIX", "AWS_REGION")
BUCKET = require(cfg, "LAB9_BUCKET")
IMAGE_DIR = Path(optional(cfg, "LAB9_IMAGE_DIR", "images")).expanduser()

# One key per subject, so lab9_rekognition.py can say "the faces image" rather
# than carrying its own copy of the file names.
IMAGE_KEYS = image_keys(cfg)

# Which analysis each image is for, per the lab sheet's pairing. Printed when
# one is missing so it is obvious which picture to go and save.
SUBJECTS = {
    "urban": "an urban setting - label recognition",
    "beach": "a person on a beach - image moderation",
    "faces": "people showing their faces - facial analysis",
    "text": "an image containing text - text extraction",
}

s3 = boto3.client("s3", region_name=AWS_REGION)


# ------------------------------------------------------------ local sources
def source_for(key: str) -> Path:
    """Find the local file for an object key, whatever extension it was saved with.

    urban.jpg matches images/urban.jpg, images/urban.jpeg or images/urban.png.
    """
    stem = Path(key).stem
    for suffix in ACCEPTED_SUFFIXES:
        candidate = IMAGE_DIR / f"{stem}{suffix}"
        if candidate.is_file():
            return candidate
    return IMAGE_DIR / key  # the path to name in the error message


def image_format(data: bytes) -> str:
    """'JPEG', 'PNG' or '' from the file's magic bytes, not its extension."""
    if data[:3] == b"\xff\xd8\xff":
        return "JPEG"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "PNG"
    return ""


def dimensions(data: bytes) -> tuple:
    """(width, height) parsed from the header, or (0, 0) if it cannot be read.

    Done by hand rather than with Pillow: the venv from Lab 1 does not have
    Pillow, and one dependency for two integers is not worth it.
    """
    kind = image_format(data)

    if kind == "PNG":
        # IHDR is always the first chunk: width and height are big-endian
        # uint32 at byte offsets 16 and 20.
        if len(data) >= 24:
            return (int.from_bytes(data[16:20], "big"),
                    int.from_bytes(data[20:24], "big"))
        return (0, 0)

    if kind == "JPEG":
        # Walk the marker segments to the frame header, which carries the size.
        i = 2
        end = len(data)
        while i + 9 < end:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            length = int.from_bytes(data[i + 2:i + 4], "big")
            # SOF0-SOF15, excluding the DHT/JPG/DAC markers interleaved with them.
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                return (int.from_bytes(data[i + 7:i + 9], "big"),
                        int.from_bytes(data[i + 5:i + 7], "big"))
            i += 2 + length
        return (0, 0)

    return (0, 0)


def content_type(kind: str) -> str:
    return {"JPEG": "image/jpeg", "PNG": "image/png"}.get(kind, "application/octet-stream")


def validate(subject: str, key: str) -> tuple:
    """Return (path, bytes, content_type) or (None, None, reason) for one image."""
    path = source_for(key)
    if not path.is_file():
        return (None, None, f"not found - expected {path} ({SUBJECTS[subject]})")

    data = path.read_bytes()
    if not data:
        return (None, None, f"{path} is empty")

    kind = image_format(data)
    if not kind:
        return (None, None,
                f"{path} is not a JPEG or PNG. Rekognition accepts only those two; "
                "a WebP or HEIC saved with a .jpg name will be rejected.")

    if len(data) > MAX_IMAGE_BYTES:
        return (None, None,
                f"{path} is {len(data) / 1048576:.1f} MB. Rekognition accepts at "
                f"most {MAX_IMAGE_BYTES // 1048576} MB for an S3-referenced image.")

    width, height = dimensions(data)
    if width and height and min(width, height) < MIN_DIMENSION:
        return (None, None,
                f"{path} is {width}x{height}. Rekognition needs at least "
                f"{MIN_DIMENSION} pixels on each side.")

    size_note = f"{kind} {width}x{height}, {len(data) / 1024:.0f} KB" if width \
        else f"{kind}, {len(data) / 1024:.0f} KB"
    print(f"  [ ok ] {key:<12} {path}  ({size_note})")
    return (path, data, content_type(kind))


# ------------------------------------------------------------------- bucket
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
                "S3 bucket names are globally unique. Change LAB9_BUCKET in "
                "lab.env and re-run."
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

    s3.create_bucket(**params)
    print(f"Created bucket {BUCKET} in {AWS_REGION}.")


def object_exists(key: str) -> bool:
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NoSuchBucket"):
            return False
        raise


def upload(key: str, data: bytes, ctype: str, force: bool) -> bool:
    if not force and object_exists(key):
        print(f"  s3://{BUCKET}/{key} already there - skipped (--force to replace)")
        return True
    s3.put_object(Bucket=BUCKET, Key=key, Body=data, ContentType=ctype)
    print(f"  uploaded s3://{BUCKET}/{key}  ({ctype}, {len(data):,} bytes)")
    return True


def listing() -> None:
    response = s3.list_objects_v2(Bucket=BUCKET)
    contents = response.get("Contents", [])
    if not contents:
        print("  (empty)")
        return
    for obj in contents:
        when = obj.get("LastModified")
        stamp = f"{when:%Y-%m-%d %H:%M}" if when else " " * 16
        print(f"  {obj['Size']:>12,}  {stamp}  {obj['Key']}")
    print(f"\n  {len(contents)} object(s) in s3://{BUCKET}/")


# ---------------------------------------------------------------------- run
def main() -> None:
    ap = argparse.ArgumentParser(description="Lab 9 S3 bucket and images.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true",
                   help="validate the local image files without touching AWS")
    g.add_argument("--list", action="store_true",
                   help="list what is currently in the bucket")
    ap.add_argument("--force", action="store_true",
                    help="re-upload images that are already in the bucket")
    args = ap.parse_args()

    report({
        "LAB_PREFIX": LAB_PREFIX,
        "AWS_REGION": AWS_REGION,
        "LAB9_BUCKET": BUCKET,
        "LAB9_IMAGE_DIR": str(IMAGE_DIR.resolve() if IMAGE_DIR.exists() else IMAGE_DIR),
        "Object keys": ", ".join(IMAGE_KEYS[s] for s in IMAGE_KEYS),
    })

    if args.list:
        if not bucket_exists():
            sys.exit(f"Bucket {BUCKET} does not exist. Run this script without --list.")
        listing()
        return

    if not IMAGE_DIR.is_dir():
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Created {IMAGE_DIR}/ - it is empty.")

    print(f"Checking the four images in {IMAGE_DIR}/")
    prepared, problems = {}, []
    for subject, key in IMAGE_KEYS.items():
        path, data, info = validate(subject, key)
        if path is None:
            print(f"  [FAIL] {key:<12} {info}")
            problems.append(key)
        else:
            prepared[key] = (data, info)
    print()

    if problems:
        print(f"Fix these in {IMAGE_DIR}/ before uploading:")
        for subject, key in IMAGE_KEYS.items():
            if key in problems:
                print(f"  {key:<12} {SUBJECTS[subject]}")
        print()
        print("The lab sheet links a source image for each one. Any .jpg, .jpeg")
        print("or .png file with the right stem is picked up; it is stored under")
        print("the key above regardless of the source extension.")
        sys.exit(1)

    if args.check:
        print("All four images are valid. Re-run without --check to upload them.")
        return

    try:
        ensure_bucket()
    except NoCredentialsError:
        sys.exit("No AWS credentials. Run 'aws configure' (Lab 1) first.")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("InvalidSignatureException", "SignatureDoesNotMatch",
                    "RequestTimeTooSkewed"):
            sys.exit("Signature rejected - WSL2's clock has drifted. "
                     "Fix it with:  sudo hwclock -s")
        raise
    print()

    for key, (data, ctype) in prepared.items():
        upload(key, data, ctype, args.force)
    print()

    listing()
    print()
    print("Next:  python3 lab9_rekognition.py all")


if __name__ == "__main__":
    main()
