#!/usr/bin/env bash
# CITS5503 Lab 9 - read-only rehearsal of every stage you have to demonstrate.
#
# Creates nothing and deletes nothing. The Comprehend and Rekognition calls it
# makes are the same read-only Detect* calls the lab scripts make. Run it right
# before the demo so a missing image or a refused permission shows up here
# rather than in front of the facilitator.
#
#   ./lab9_verify.sh

set -uo pipefail   # deliberately not -e: every stage should report, not abort

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lab9_lib.sh
source "${HERE}/lab9_lib.sh"

lab_env_load
lab_env_require LAB_PREFIX AWS_REGION LAB9_BUCKET LAB9_IMAGE_DIR \
                LAB9_IMAGE_URBAN LAB9_IMAGE_BEACH LAB9_IMAGE_FACES \
                LAB9_IMAGE_TEXT LAB9_MIN_CONFIDENCE LAB9_MAX_LABELS

pass=0; fail=0
ok()   { printf '  [ ok ] %s\n' "$*"; pass=$((pass+1)); }
no()   { printf '  [FAIL] %s\n' "$*"; fail=$((fail+1)); }
info() { printf '         %s\n' "$*"; }

hdr() {
  echo
  echo "--------------------------------------------------------------"
  echo " $*"
  echo "--------------------------------------------------------------"
}

# tally OUTPUT EXIT_STATUS - show an embedded Python check's output and fold its
# [ ok ]/[FAIL] lines into this script's totals. The exit status is passed in
# separately: a check that dies before printing anything (no credentials, no
# network, an unhandled exception) would otherwise contribute nothing and look
# like a clean pass.
tally() {
  local out="$1" rc="$2" seen
  printf '%s\n' "${out}"
  pass=$(( pass + $(grep -c '\[ ok \]' <<<"${out}") ))
  seen=$(grep -c '\[FAIL\]' <<<"${out}")
  fail=$(( fail + seen ))
  if (( rc != 0 && seen == 0 )); then
    no "the check above did not complete (exit ${rc})"
  fi
}

echo "=============================================================="
echo " CITS5503 Lab 9 verification - ${LAB_PREFIX} in ${AWS_REGION}"
echo "=============================================================="

# ============================================= Setup: files and configuration
hdr "Setup  Scripts and configuration"

for f in lab9_env.py lab9_lib.sh lab9_texts.py lab9_languages.py \
         lab9_comprehend.py lab9_bucket.py lab9_rekognition.py lab9_cleanup.py; do
  if [[ -f "${HERE}/${f}" ]]; then
    ok "${f} present"
  else
    no "${f} missing"
  fi
done

if [[ "${LAB9_BUCKET}" == "${LAB_PREFIX}-lab9" ]]; then
  ok "LAB9_BUCKET is ${LAB9_BUCKET}, derived from LAB_PREFIX"
else
  no "LAB9_BUCKET is '${LAB9_BUCKET}', not '${LAB_PREFIX}-lab9'"
  info "re-run ./lab9_prepare.sh"
fi

case "${AWS_REGION}" in
  eu-north-1|ca-central-1)
    no "${AWS_REGION} does not fully support Comprehend and Rekognition"
    info "the lab sheet says to use ap-southeast-2"
    ;;
  *) ok "region ${AWS_REGION} is usable for Comprehend and Rekognition" ;;
esac

if aws sts get-caller-identity --region "${AWS_REGION}" >/dev/null 2>&1; then
  ok "AWS credentials work"
else
  no "aws sts get-caller-identity failed"
  info "if the error mentions a signature, run:  sudo hwclock -s"
fi

# ================================= Checkpoint 1: language detection
hdr "Checkpoint 1  Language detection, all four texts"

out="$(cd "${HERE}" && python3 - <<'PY' 2>&1
import sys
sys.path.insert(0, ".")
try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    print("  [FAIL] boto3 not importable - activate the venv:  labenv")
    raise SystemExit(1)

from lab9_env import load_env, require
from lab9_texts import TEXTS, EXPECTED_LANGUAGES as EXPECTED
from lab9_languages import language_name, as_percentage

cfg = load_env()
region = require(cfg, "AWS_REGION")
client = boto3.client("comprehend", region_name=region)

for label, text in TEXTS.items():
    try:
        languages = client.detect_dominant_language(Text=text)["Languages"]
    except ClientError as exc:
        print(f"  [FAIL] {label}: {exc.response['Error']['Code']}")
        continue
    except Exception as exc:
        # Credentials, DNS and clock problems all surface here rather than as a
        # ClientError, and they must not pass silently.
        print(f"  [FAIL] {label}: {type(exc).__name__}: {exc}")
        continue
    if not languages:
        print(f"  [FAIL] {label}: no language returned")
        continue
    top = languages[0]
    code, percent = top["LanguageCode"], as_percentage(top["Score"])
    message = f"{language_name(code)} was detected with {percent}% confidence"
    if code == EXPECTED[label]:
        print(f"  [ ok ] {label:<8} {message}")
    else:
        print(f"  [FAIL] {label:<8} expected '{EXPECTED[label]}', got '{code}' - {message}")
PY
)"; tally "${out}" "$?"

# ============= Checkpoints 2-4: sentiment, entities, keyphrases (and syntax)
hdr "Checkpoints 2-4  Sentiment, entities, key phrases (and syntax)"

out="$(cd "${HERE}" && python3 - <<'PY' 2>&1
import sys
sys.path.insert(0, ".")
import boto3
from botocore.exceptions import ClientError

from lab9_env import load_env, require
from lab9_texts import TEXTS, EXPECTED_LANGUAGES as CODES

cfg = load_env()
region = require(cfg, "AWS_REGION")
client = boto3.client("comprehend", region_name=region)

CALLS = [
    ("sentiment",  "detect_sentiment",  lambda r: f"{r['Sentiment'].lower()}"),
    ("entities",   "detect_entities",   lambda r: f"{len(r['Entities'])} entities"),
    ("keyphrases", "detect_key_phrases", lambda r: f"{len(r['KeyPhrases'])} key phrases"),
    ("syntax",     "detect_syntax",     lambda r: f"{len(r['SyntaxTokens'])} tokens"),
]

for label, text in TEXTS.items():
    code = CODES[label]
    results = []
    failed = False
    for name, method, summarise in CALLS:
        try:
            response = getattr(client, method)(Text=text, LanguageCode=code)
            results.append(f"{name}: {summarise(response)}")
        except ClientError as exc:
            print(f"  [FAIL] {label:<8} {name} - {exc.response['Error']['Code']}")
            failed = True
        except Exception as exc:
            print(f"  [FAIL] {label:<8} {name} - {type(exc).__name__}: {exc}")
            failed = True
    if not failed:
        print(f"  [ ok ] {label:<8} {'; '.join(results)}")
PY
)"; tally "${out}" "$?"

# ==================== Checkpoints 1-4: the prompt path the facilitator will use
hdr "Checkpoints 1-4  The prompt path, end to end"

echo "  Feeding a text on stdin exercises exactly what happens when the"
echo "  facilitator pastes one at the prompt, including the exit status."
echo

for pair in "language:English:was detected with" \
            "sentiment:Spanish:Sentiment score:" \
            "entities:French:CONF" \
            "keyphrases:Italian:key phrase"; do
  which="${pair%%:*}"
  rest="${pair#*:}"
  label="${rest%%:*}"
  marker="${rest#*:}"

  text="$(cd "${HERE}" && python3 -c \
    "import sys; sys.path.insert(0, '.'); from lab9_texts import TEXTS; print(TEXTS['${label}'])" \
    2>/dev/null)"
  if [[ -z "${text}" ]]; then
    no "could not load the ${label} text from lab9_texts.py"
    continue
  fi

  out="$(cd "${HERE}" && printf '%s\n' "${text}" | python3 lab9_comprehend.py "${which}" 2>&1)"
  rc=$?

  if (( rc != 0 )); then
    no "${which} on the ${label} text exited ${rc}, not 0"
    printf '%s\n' "${out}" | tail -3 | sed 's/^/         /'
  elif grep -q "${marker}" <<<"${out}"; then
    ok "${which} prompted, read the ${label} text, printed a result, exited 0"
  else
    no "${which} exited 0 but printed no '${marker}' line"
    printf '%s\n' "${out}" | tail -5 | sed 's/^/         /'
  fi
done

# ============================== Checkpoint 5, part one: the bucket and images
hdr "Checkpoint 5a  S3 bucket and the four images"

if aws s3api head-bucket --bucket "${LAB9_BUCKET}" --region "${AWS_REGION}" >/dev/null 2>&1; then
  ok "bucket ${LAB9_BUCKET} exists"

  loc="$(aws s3api get-bucket-location --bucket "${LAB9_BUCKET}" \
           --query 'LocationConstraint' --output text 2>/dev/null)"
  [[ "${loc}" == "None" ]] && loc="us-east-1"
  if [[ "${loc}" == "${AWS_REGION}" ]]; then
    ok "bucket is in ${AWS_REGION}, the same region as the Rekognition calls"
  else
    no "bucket is in ${loc} but Rekognition is being called in ${AWS_REGION}"
    info "Rekognition can only read an S3 object in its own region"
  fi

  for key in "${LAB9_IMAGE_URBAN}" "${LAB9_IMAGE_BEACH}" "${LAB9_IMAGE_FACES}" "${LAB9_IMAGE_TEXT}"; do
    size="$(aws s3api head-object --bucket "${LAB9_BUCKET}" --key "${key}" \
              --region "${AWS_REGION}" --query 'ContentLength' --output text 2>/dev/null)"
    if [[ -n "${size}" && "${size}" != "None" ]]; then
      ok "${key} present ($(awk -v s="${size}" 'BEGIN{printf "%.0f", s/1024}') KB)"
    else
      no "${key} not in the bucket - run python3 lab9_bucket.py"
    fi
  done
else
  no "bucket ${LAB9_BUCKET} not found - run python3 lab9_bucket.py"
fi

# ========================== Checkpoint 5, part two: one analysis per image
hdr "Checkpoint 5b  Rekognition, one analysis per image"

out="$(cd "${HERE}" && python3 - <<'PY' 2>&1
import sys
sys.path.insert(0, ".")
import boto3
from botocore.exceptions import ClientError

from lab9_env import load_env, require, optional, image_keys

cfg = load_env()
region, bucket = require(cfg, "AWS_REGION", "LAB9_BUCKET")
keys = image_keys(cfg)
min_conf = float(optional(cfg, "LAB9_MIN_CONFIDENCE", "80"))
max_labels = int(optional(cfg, "LAB9_MAX_LABELS", "20"))

client = boto3.client("rekognition", region_name=region)


def image(key):
    return {"S3Object": {"Bucket": bucket, "Name": key}}


def check(name, subject, call, expect_nonempty):
    key = keys[subject]
    try:
        count, detail = call(key)
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        print(f"  [FAIL] {name:<11} {key:<11} {code}")
        if code == "InvalidS3ObjectException":
            print(f"         Rekognition cannot read s3://{bucket}/{key}")
        return
    except Exception as exc:
        print(f"  [FAIL] {name:<11} {key:<11} {type(exc).__name__}: {exc}")
        return
    verdict = "[ ok ]" if (count > 0 or not expect_nonempty) else "[FAIL]"
    print(f"  {verdict} {name:<11} {key:<11} {detail}")
    if verdict == "[FAIL]":
        print(f"         expected at least one result - is {key} the right picture?")


# The lab sheet's pairing: one analysis per image, and only that one.
check("labels", "urban",
      lambda k: (lambda r: (len(r["Labels"]),
                            f"{len(r['Labels'])} labels, top: " +
                            ", ".join(l["Name"] for l in r["Labels"][:4])))(
          client.detect_labels(Image=image(k), MaxLabels=max_labels,
                               MinConfidence=min_conf)),
      expect_nonempty=True)

# An empty moderation result is a valid, expected outcome, so this one only
# has to answer - it does not have to find anything.
check("moderation", "beach",
      lambda k: (lambda r: (len(r["ModerationLabels"]),
                            f"{len(r['ModerationLabels'])} moderation label(s)" +
                            (": " + ", ".join(m["Name"] for m in r["ModerationLabels"][:3])
                             if r["ModerationLabels"] else " (nothing flagged)")))(
          client.detect_moderation_labels(Image=image(k), MinConfidence=min_conf)),
      expect_nonempty=False)

check("faces", "faces",
      lambda k: (lambda r: (len(r["FaceDetails"]),
                            f"{len(r['FaceDetails'])} face(s) with full attributes"))(
          client.detect_faces(Image=image(k), Attributes=["ALL"])),
      expect_nonempty=True)

check("text", "text",
      lambda k: (lambda r: (len([d for d in r["TextDetections"] if d["Type"] == "LINE"]),
                            f"{len([d for d in r['TextDetections'] if d['Type'] == 'LINE'])} line(s), "
                            f"{len([d for d in r['TextDetections'] if d['Type'] == 'WORD'])} word(s)"))(
          client.detect_text(Image=image(k))),
      expect_nonempty=True)
PY
)"; tally "${out}" "$?"

# ================================================== Cleanup readiness
hdr "Cleanup"

echo "  Nothing is deleted by this script. When the demo is done:"
echo "    python3 lab9_cleanup.py"
echo "  which empties and deletes s3://${LAB9_BUCKET}. Comprehend and"
echo "  Rekognition leave nothing behind - every call in this lab is"
echo "  synchronous and stateless, so the bucket is the only resource."

echo
echo "=============================================================="
printf ' %d passed, %d failed\n' "${pass}" "${fail}"
echo "=============================================================="
(( fail == 0 )) || exit 1
