#!/usr/bin/env python3
"""CITS5503 Lab 9 - the AWS Rekognition half of the lab.

The lab sheet pairs each image with exactly one analysis, and asks for only that
analysis on that image:

    urban.jpg   label recognition
    beach.jpg   image moderation
    faces.jpg   facial analysis
    text.jpg    text extraction

That pairing is what Checkpoint 5 asks you to run, and it is what the default
does:

    labenv
    python3 lab9_rekognition.py all      all four pairs, then exit

One pair at a time, for practice:

    python3 lab9_rekognition.py labels
    python3 lab9_rekognition.py moderation
    python3 lab9_rekognition.py faces
    python3 lab9_rekognition.py text

    python3 lab9_rekognition.py labels --image beach   deliberately off-pairing
    python3 lab9_rekognition.py text --json            the raw API response

Each subcommand runs on its own paired image unless --image says otherwise.
--image exists for exploring what the other pictures return; it is not what the
checkpoint wants.

The images are read from the S3 bucket by reference (Image={'S3Object': ...}),
not uploaded again as bytes, so the bucket built by lab9_bucket.py is what is
actually being analysed.
"""

import argparse
import json
import sys

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
except ImportError:
    sys.exit("boto3 not found. Activate the shared lab venv first:  labenv")

from lab9_env import load_env, require, optional, image_keys, report

cfg = load_env()
LAB_PREFIX, AWS_REGION = require(cfg, "LAB_PREFIX", "AWS_REGION")
BUCKET = require(cfg, "LAB9_BUCKET")

IMAGE_KEYS = image_keys(cfg)

MIN_CONFIDENCE = float(optional(cfg, "LAB9_MIN_CONFIDENCE", "80"))
MAX_LABELS = int(optional(cfg, "LAB9_MAX_LABELS", "20"))

# The lab sheet's pairing: each analysis runs on exactly one image, and each
# image gets exactly one analysis. This is the whole of Checkpoint 5.
PAIRED_SUBJECT = {
    "labels": "urban",
    "moderation": "beach",
    "faces": "faces",
    "text": "text",
}

rekognition = boto3.client("rekognition", region_name=AWS_REGION)


def s3_image(key: str) -> dict:
    return {"S3Object": {"Bucket": BUCKET, "Name": key}}


# --------------------------------------------------------------- formatting
def banner(title: str) -> None:
    print()
    print("=" * 72)
    print(f" {title}")
    print("=" * 72)


def image_header(subject: str, key: str) -> None:
    print()
    print(f"--- {key} " + "-" * max(0, 68 - len(key)))
    print(f"    s3://{BUCKET}/{key}   ({subject})")
    print()


def dump(response: dict) -> None:
    response = {k: v for k, v in response.items() if k != "ResponseMetadata"}
    print(json.dumps(response, indent=2, ensure_ascii=False, default=str))


def box(bb: dict) -> str:
    """Bounding box as percentages of the image, which is how Rekognition gives it."""
    return (f"x {bb['Left'] * 100:5.1f}% y {bb['Top'] * 100:5.1f}% "
            f"w {bb['Width'] * 100:5.1f}% h {bb['Height'] * 100:5.1f}%")


def yes_no(attr: dict) -> str:
    """A boolean attribute plus the model's confidence in it."""
    return f"{'yes' if attr['Value'] else 'no':<4} ({attr['Confidence']:.1f}%)"


# ------------------------------------------------------- 1. label detection
def detect_labels(key: str) -> dict:
    return rekognition.detect_labels(
        Image=s3_image(key),
        MaxLabels=MAX_LABELS,
        MinConfidence=MIN_CONFIDENCE,
    )


def show_labels(key: str, raw: bool) -> None:
    response = detect_labels(key)
    if raw:
        dump(response)
        return

    labels = response["Labels"]
    print(f"    {len(labels)} label(s) at or above {MIN_CONFIDENCE:.0f}% confidence "
          f"(model {response.get('LabelModelVersion', '?')}):")
    if not labels:
        print("      none - try lowering LAB9_MIN_CONFIDENCE in lab.env")
        return

    print(f"      {'CONF':>7}  {'LABEL':<22}{'INST':>5}  CATEGORIES / PARENTS")
    for label in labels:
        instances = len(label.get("Instances", []))
        categories = [c["Name"] for c in label.get("Categories", [])]
        parents = [p["Name"] for p in label.get("Parents", [])]
        detail = ", ".join(categories) or "-"
        if parents:
            detail += f"  < {' < '.join(parents)}"
        print(f"      {label['Confidence']:6.1f}%  {label['Name']:<22}"
              f"{instances if instances else '':>5}  {detail}")

    # Instances are the labels Rekognition could actually localise in the frame.
    located = [l for l in labels if l.get("Instances")]
    if located:
        print()
        print("      located instances (bounding boxes):")
        for label in located:
            for inst in label["Instances"]:
                print(f"        {label['Name']:<18}{inst['Confidence']:6.1f}%  "
                      f"{box(inst['BoundingBox'])}")


# ---------------------------------------------------- 2. image moderation
def detect_moderation_labels(key: str) -> dict:
    return rekognition.detect_moderation_labels(
        Image=s3_image(key),
        MinConfidence=MIN_CONFIDENCE,
    )


def show_moderation(key: str, raw: bool) -> None:
    response = detect_moderation_labels(key)
    if raw:
        dump(response)
        return

    labels = response["ModerationLabels"]
    print(f"    moderation model {response.get('ModerationModelVersion', '?')}, "
          f"threshold {MIN_CONFIDENCE:.0f}%")

    if not labels:
        print("      no moderation labels - nothing flagged as explicit, "
              "suggestive or violent")
        return

    print()
    print(f"      {'CONF':>7}  {'LABEL':<28}{'LEVEL':>6}  PARENT")
    for label in labels:
        parent = label.get("ParentName") or "(top level)"
        level = label.get("TaxonomyLevel", "")
        print(f"      {label['Confidence']:6.1f}%  {label['Name']:<28}"
              f"{level:>6}  {parent}")
    print()
    print("      A hit here is the model's judgement, not a verdict. The beach")
    print("      image typically comes back under a swimwear/suggestive label,")
    print("      which is what the threshold is for.")


# ------------------------------------------------------- 3. facial analysis
def detect_faces(key: str) -> dict:
    # 'ALL' returns every attribute; the default returns only the bounding box,
    # quality, pose and confidence, which is not enough for this section.
    return rekognition.detect_faces(Image=s3_image(key), Attributes=["ALL"])


def show_faces(key: str, raw: bool) -> None:
    response = detect_faces(key)
    if raw:
        dump(response)
        return

    faces = response["FaceDetails"]
    print(f"    {len(faces)} face(s) detected")
    if not faces:
        print("      none - detect_faces returns the 100 largest faces in the frame")
        return

    for i, face in enumerate(faces, start=1):
        age = face["AgeRange"]
        emotions = sorted(face["Emotions"], key=lambda e: -e["Confidence"])
        top = ", ".join(f"{e['Type'].title()} {e['Confidence']:.0f}%" for e in emotions[:3])

        print()
        print(f"      Face {i}  (detected with {face['Confidence']:.1f}% confidence)")
        print(f"        position     {box(face['BoundingBox'])}")
        print(f"        age range    {age['Low']}-{age['High']} years")
        print(f"        emotions     {top}")
        print(f"        smiling      {yes_no(face['Smile'])}")
        print(f"        eyes open    {yes_no(face['EyesOpen'])}")
        print(f"        mouth open   {yes_no(face['MouthOpen'])}")
        print(f"        eyeglasses   {yes_no(face['Eyeglasses'])}")
        print(f"        sunglasses   {yes_no(face['Sunglasses'])}")
        print(f"        beard        {yes_no(face['Beard'])}")
        print(f"        moustache    {yes_no(face['Mustache'])}")

        if "Gender" in face:
            g = face["Gender"]
            print(f"        gender       {g['Value']} ({g['Confidence']:.1f}%) "
                  "- a prediction of appearance, not an identity")

        occluded = face.get("FaceOccluded")
        if occluded:
            print(f"        occluded     {yes_no(occluded)}")

        pose = face["Pose"]
        print(f"        pose         pitch {pose['Pitch']:.1f}, "
              f"roll {pose['Roll']:.1f}, yaw {pose['Yaw']:.1f} degrees")

        quality = face["Quality"]
        print(f"        quality      brightness {quality['Brightness']:.1f}, "
              f"sharpness {quality['Sharpness']:.1f}")
        print(f"        landmarks    {len(face.get('Landmarks', []))} points")

    print()
    print("      detect_faces analyses faces; it does not identify anyone. Matching")
    print("      a face against a collection is a different API (search_faces).")


# ------------------------------------------------------ 4. text extraction
def detect_text(key: str) -> dict:
    return rekognition.detect_text(Image=s3_image(key))


def show_text(key: str, raw: bool) -> None:
    response = detect_text(key)
    if raw:
        dump(response)
        return

    detections = response["TextDetections"]
    lines = [d for d in detections if d["Type"] == "LINE"]
    words = [d for d in detections if d["Type"] == "WORD"]

    print(f"    {len(lines)} line(s) and {len(words)} word(s) "
          f"(model {response.get('TextModelVersion', '?')})")
    if not detections:
        print("      no text found")
        return

    print()
    print("      Lines, in the order Rekognition returned them:")
    print(f"      {'ID':>4}  {'CONF':>7}  TEXT")
    for line in lines:
        print(f"      {line['Id']:>4}  {line['Confidence']:6.1f}%  {line['DetectedText']}")

    low = [w for w in words if w["Confidence"] < MIN_CONFIDENCE]
    if low:
        print()
        print(f"      {len(low)} word(s) below {MIN_CONFIDENCE:.0f}% confidence:")
        for w in low:
            print(f"        {w['Confidence']:6.1f}%  {w['DetectedText']}")

    print()
    print("      Reconstructed text:")
    for line in lines:
        print(f"        {line['DetectedText']}")


# ----------------------------------------------------------------- the runner
ANALYSES = {
    "labels": ("Label recognition", show_labels),
    "moderation": ("Image moderation", show_moderation),
    "faces": ("Facial analysis", show_faces),
    "text": ("Text extraction", show_text),
}


def run(which: str, subjects: list, raw: bool) -> int:
    """Run one analysis over the given images. Returns the number of failures."""
    title, handler = ANALYSES[which]
    banner(f"{title}  (bucket {BUCKET}, region {AWS_REGION})")
    failures = 0

    for subject in subjects:
        key = IMAGE_KEYS[subject]
        image_header(subject, key)
        try:
            handler(key, raw)
        except ClientError as exc:
            error = exc.response["Error"]
            code = error["Code"]
            print(f"    [FAIL] {code}: {error['Message']}")
            failures += 1
            if code == "InvalidS3ObjectException":
                print(f"           Rekognition cannot read s3://{BUCKET}/{key}.")
                print("           Check the object exists and is in the same region "
                      "as the Rekognition call:")
                print("             python3 lab9_bucket.py --list")
            elif code == "InvalidImageFormatException":
                print("           The object is not a valid JPEG or PNG. Re-upload it:")
                print("             python3 lab9_bucket.py --force")
            elif code == "AccessDeniedException":
                print("           Your account may not have rekognition:Detect* "
                      "permissions.")
            elif code == "ImageTooLargeException":
                print("           The image is over Rekognition's 15 MB S3 limit.")

    return failures


def main() -> None:
    ap = argparse.ArgumentParser(
        description="AWS Rekognition analyses for CITS5503 Lab 9.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Images: " + ", ".join(f"{s} = {k}" for s, k in IMAGE_KEYS.items()),
    )
    ap.add_argument("analysis", choices=list(ANALYSES) + ["all"],
                    help="which capability to test; 'all' runs the four pairs")
    ap.add_argument("--image", action="append", choices=list(IMAGE_KEYS),
                    metavar="SUBJECT",
                    help=f"override the paired image ({', '.join(IMAGE_KEYS)}); "
                         "repeatable, and not what the checkpoint asks for")
    ap.add_argument("--json", action="store_true",
                    help="print the raw API response instead of a summary")
    args = ap.parse_args()

    if args.image and args.analysis == "all":
        ap.error("--image applies to a single analysis, not to 'all'.")

    report({
        "LAB_PREFIX": LAB_PREFIX,
        "AWS_REGION": AWS_REGION,
        "LAB9_BUCKET": BUCKET,
        "Analysis": args.analysis,
        "Images": ", ".join(args.image) if args.image
                  else ", ".join(f"{a} on {IMAGE_KEYS[PAIRED_SUBJECT[a]]}"
                                 for a in (list(ANALYSES) if args.analysis == "all"
                                           else [args.analysis])),
        "Min confidence": f"{MIN_CONFIDENCE:.0f}%",
        "Max labels": MAX_LABELS,
    })

    # Fail on the bucket rather than four times on the images.
    s3 = boto3.client("s3", region_name=AWS_REGION)
    try:
        s3.head_bucket(Bucket=BUCKET)
    except NoCredentialsError:
        sys.exit("No AWS credentials. Run 'aws configure' (Lab 1) first.")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("404", "NoSuchBucket"):
            sys.exit(f"Bucket {BUCKET} does not exist. Run:  python3 lab9_bucket.py")
        if code in ("InvalidSignatureException", "SignatureDoesNotMatch",
                    "RequestTimeTooSkewed"):
            sys.exit("Signature rejected - WSL2's clock has drifted. "
                     "Fix it with:  sudo hwclock -s")
        # Anything else: a message, not a traceback.
        sys.exit(f"Cannot reach s3://{BUCKET}: {code}: "
                 f"{exc.response['Error'].get('Message', '')}")

    order = list(ANALYSES) if args.analysis == "all" else [args.analysis]
    failures = 0
    for which in order:
        subjects = args.image or [PAIRED_SUBJECT[which]]
        failures += run(which, subjects, args.json)

    print()
    if failures:
        print(f"Done, with {failures} failure(s) above.")
        sys.exit(1)
    print("Done.")
    print("Rehearse the whole lab with:  ./lab9_verify.sh")
    print("Then empty and delete the bucket, from the console or with:")
    print("  python3 lab9_cleanup.py")


if __name__ == "__main__":
    main()
