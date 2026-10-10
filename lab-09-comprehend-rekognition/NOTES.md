# CITS5503 Lab 9 - Comprehend and Rekognition

> Working notes written while doing this lab, aimed at the in-lab demo and its
> checkpoints. Identifiers are placeholders (`<prefix>` = `LAB_PREFIX`), and paths
> assume the scripts were copied into `~/cits5503/labN/`. Start with
> [README.md](README.md) for the overview.

Test the main features of AWS Comprehend (language detection, sentiment,
entities, key phrases, syntax) and AWS Rekognition (labels, moderation, faces,
text) against four images in an S3 bucket.

Prefix `<prefix>`, region `ap-southeast-2`, Windows 11 + WSL2 (Ubuntu
26.04).

---

## Files

| File | Role |
|---|---|
| `lab9_env.py` | lab.env reader/writer for the Python scripts |
| `lab9_lib.sh` | lab.env loader/writer for the shell scripts |
| `lab9_prepare.sh` | local checks (CLI, boto3, credentials, region support, WSL2 clock and DNS) and the only writer of the Lab 9 keys |
| `lab9_texts.py` | the four lab texts, their sources, and what language each one actually is |
| `lab9_languages.py` | language code to English name, and the confidence-to-percentage conversion |
| `lab9_comprehend.py` | all five Comprehend sections; prompts for a text by default |
| `lab9_bucket.py` | create `<prefix>-lab9`, validate and upload the four images |
| `lab9_rekognition.py` | the four Rekognition analyses, one per image |
| `lab9_verify.sh` | read-only rehearsal of every checkpoint |
| `lab9_cleanup.py` | empty and delete the bucket |
| `images/` | where you put the four source images |

---

## Order to run

```bash
labenv                              # the shared Lab 1 virtualenv

./lab9_prepare.sh                   # checks, and seeds lab.env

# practise the Comprehend half against all four texts
python3 lab9_comprehend.py all --all-texts

# save the four images into images/ first
python3 lab9_bucket.py --check      # validate them offline
python3 lab9_bucket.py              # create the bucket and upload

python3 lab9_rekognition.py all     # one analysis per image

./lab9_verify.sh                    # rehearse every checkpoint
python3 lab9_cleanup.py
```

---

## The checkpoints

### Checkpoints 1-4: language, sentiment, entities, key phrases

Each one runs the same way. The script prompts, the facilitator picks one of the
four texts at random, you paste it, the script prints its result and exits.

```bash
python3 lab9_comprehend.py language     # Checkpoint 1
python3 lab9_comprehend.py sentiment    # Checkpoint 2
python3 lab9_comprehend.py entities     # Checkpoint 3
python3 lab9_comprehend.py keyphrases   # Checkpoint 4
```

With no input flags the script prompts. That is deliberate: prompting is the
demonstrated behaviour, so it is the default rather than something you have to
remember a flag for.

What each one prints:

| Checkpoint | Output |
|---|---|
| 1 | the language name in English and a confidence percentage: `English was detected with 99% confidence` |
| 2 | the sentiment category, and the score as a dictionary over all four categories |
| 3 | each detected entity: its text, its type, its confidence score |
| 4 | each key phrase and its confidence score |

The prompt reads one line, because each of the four texts occupies one line in
the lab sheet and pasting one delivers one line however the terminal wraps it.
`--multiline` reads until a blank line instead, for a text that genuinely
contains newlines.

The language code is detected, not assumed. Sentiment, entities and key phrases
all need a `LanguageCode`, so the pasted text goes through
`detect_dominant_language` first and the winning code is what gets sent. That is
what makes a randomly chosen text work without anyone saying which it is.
`--language it` forces one if you ever need to.

The service is checked for reachability *before* the prompt appears, so a
credentials or clock problem surfaces before the facilitator pastes anything
rather than after.

### Checkpoint 5: the bucket, and one analysis per image

First show the bucket in the console: S3 → `<prefix>-lab9`, containing
`urban.jpg`, `beach.jpg`, `faces.jpg`, `text.jpg`.

Then:

```bash
python3 lab9_rekognition.py all
```

The lab sheet pairs each image with exactly one analysis and asks for only that
analysis on that image. That pairing is the default, and `all` runs the four
pairs and exits:

| Image | Analysis | Output |
|---|---|---|
| `urban.jpg` | label recognition | each label and its confidence score |
| `beach.jpg` | image moderation | each moderation label and its confidence score |
| `faces.jpg` | facial analysis | each face and its confidence score, plus the full attribute set |
| `text.jpg` | text extraction | each detected line and its confidence score |

Individually, if you want to run one at a time:

```bash
python3 lab9_rekognition.py labels        # urban.jpg
python3 lab9_rekognition.py moderation    # beach.jpg
python3 lab9_rekognition.py faces         # faces.jpg
python3 lab9_rekognition.py text          # text.jpg
```

`--image` overrides the pairing. It is there for exploring what the other
pictures return; it is not what the checkpoint asks for, and it is rejected
alongside `all`.

Last, empty and delete the bucket from the console.

---

## Practice mode

None of these prompt, so they are what you use while preparing:

```bash
python3 lab9_comprehend.py all --all-texts        all five analyses, all four texts
python3 lab9_comprehend.py sentiment --text French  one named lab text
python3 lab9_comprehend.py entities --input "..."   your own string
python3 lab9_comprehend.py syntax --file notes.txt  a file's contents
python3 lab9_comprehend.py language --json          the raw API response
```

At most one of `--all-texts`, `--text`, `--input` and `--file` may be given.

`syntax` has no checkpoint but is part of the lab body, so it is implemented and
included in `all`.

---

## The images

Save the four images from the lab sheet into `images/` before running
`lab9_bucket.py`. They are not in this bundle - the lab sheet links them from
external sites.

Two of the four figures on the lab sheet are PNGs even though the sheet names
the objects `.jpg`. A `.jpg`, `.jpeg` or `.png` file with the right stem is
accepted; the object is stored under the name the sheet specifies either way,
and Rekognition decides the format from the bytes rather than the key. The
`Content-Type` recorded on the object is the real one.

`python3 lab9_bucket.py --check` validates all four offline against what
Rekognition actually accepts: JPEG or PNG only, at least 80 pixels on each side,
under 15 MB. The format and dimensions are read from the file header by hand
rather than with Pillow, which the Lab 1 venv does not have.

---

## Design notes worth being able to explain

**The percentage is truncated, not rounded.** The lab sheet's worked example is
a score of 0.9961233 printed as 99%; rounding would give 100%, claiming total
certainty for a model that did not report 1.0. Runner-up languages print to one
decimal place instead, so a 0.2% second place does not appear as a flat 0%.

**Language support differs per API**, and is checked before the call rather than
surfacing as an `UnsupportedLanguageException` from inside a loop:

- sentiment, entities, key phrases: `en es fr de it pt ar hi ja ko zh zh-TW`
- syntax: `en es fr de it pt` only

All four lab texts fall inside both sets.

**Language names** come from a table in `lab9_languages.py` covering every code
`DetectDominantLanguage` documents. Nothing needs installing. Babel or pycountry
are used if present, for codes the table lacks, but neither is required.

**The images are read from S3 by reference** (`Image={'S3Object': ...}`), not
re-uploaded as bytes, so what gets analysed is demonstrably the bucket you
built. Rekognition can only read an S3 object in its own region, which
`lab9_verify.sh` checks explicitly.

**Both analysis scripts exit 0 on success and 1 if any analysis failed**, so a
failure is visible in the exit status rather than only in the scrollback.

---

## Configuration

Everything comes from `~/cits5503/lab.env`. Override the path with
`LAB_ENV=/some/other/path` on any script.

Read from earlier labs, never rewritten here:

| Key | Written by |
|---|---|
| `LAB_PREFIX` | Lab 1 |
| `AWS_REGION` | Lab 1 |
| `AWS_ACCOUNT_ID` | whichever lab first recorded it |

Written by `lab9_prepare.sh`, and only by it:

| Key | Value | Why |
|---|---|---|
| `LAB9_BUCKET` | `<prefix>-lab9` | derived from `LAB_PREFIX` at runtime |
| `LAB9_IMAGE_DIR` | `<this directory>/images` | where the source images live |
| `LAB9_IMAGE_URBAN` | `urban.jpg` | object key, per the lab sheet |
| `LAB9_IMAGE_BEACH` | `beach.jpg` | object key |
| `LAB9_IMAGE_FACES` | `faces.jpg` | object key |
| `LAB9_IMAGE_TEXT` | `text.jpg` | object key |
| `LAB9_MIN_CONFIDENCE` | `80` | Rekognition threshold for labels, moderation and the low-confidence word report |
| `LAB9_MAX_LABELS` | `20` | `MaxLabels` on `detect_labels` |

`lab9_prepare.sh` is safe to re-run: it replaces keys in place, writes the
section header once, and leaves `AWS_ACCOUNT_ID` alone if an earlier lab already
recorded it.

`LAB9_MIN_CONFIDENCE` is deliberately not read by `lab9_comprehend.py`. It is a
Rekognition threshold; the Comprehend `Detect*` APIs return every result with its
score and have no confidence filter to set.

### Values that are fixed rather than configured

| Value | Where | Why it is not a key |
|---|---|---|
| the image-to-analysis pairing | `lab9_rekognition.py` | mandated by the lab sheet |
| `Attributes=['ALL']` | `detect_faces` | the default returns only the bounding box and pose, which is not enough for the facial analysis section |
| 5,000 bytes | `lab9_texts.py` | Comprehend's documented limit for the synchronous `Detect*` APIs |
| 15 MB, 80 px | `lab9_bucket.py` | Rekognition's documented limits for an S3-referenced image |
| the two language sets | `lab9_comprehend.py` | fixed by the Comprehend service model, verified against botocore |
| the four texts | `lab9_texts.py` | quoted verbatim from the lab sheet |

---

## Verification

`./lab9_verify.sh` creates and deletes nothing. It checks, in order: the scripts
and configuration; language detection against all four texts, comparing each
answer to what the text actually is; sentiment, entities, key phrases and syntax
on all four; the prompt path end to end, by feeding a text on stdin and checking
both the output and the exit status; the bucket, its region and its four
objects; and the four paired Rekognition analyses.

A check that dies before printing anything counts as a failure rather than
passing silently.

---

## Troubleshooting

**`aws sts get-caller-identity` fails, or any call reports a signature error.**
WSL2's clock drifts after Windows sleeps. `sudo hwclock -s`.

**`comprehend.<region>.amazonaws.com` will not resolve.** WSL2 DNS:

```bash
sudo rm /etc/resolv.conf
echo "nameserver 8.8.8.8" | sudo tee /etc/resolv.conf
sudo chattr +i /etc/resolv.conf
```

then `wsl --shutdown` from PowerShell.

**`boto3 not found`.** Activate the shared venv first: `labenv`. A bare
`pip3 install` fails on Ubuntu 26.04 under PEP 668.

**`aws --version` says `aws-cli/1.x`.** `apt install awscli` gives v1. Install
v2 from the AWS zip installer.

**The prompt seems to swallow the paste.** The text went in on one line and was
accepted; the script echoes the first 100 characters back under `text:` so you
can confirm it got the whole thing.

**`InvalidS3ObjectException` from Rekognition.** Either the object is not there
(`python3 lab9_bucket.py --list`) or the bucket is in a different region from
the Rekognition call. `./lab9_verify.sh` checks both.

**`InvalidImageFormatException`.** The file is not really a JPEG or PNG - a WebP
or HEIC saved with a `.jpg` name is the usual cause. `lab9_bucket.py --check`
catches this before upload.

**`UnsupportedLanguageException` from Comprehend.** The detected language is
outside that API's supported set. `--language` forces a supported one.

**Comprehend or Rekognition is not available in your region.** The lab sheet
notes that `eu-north-1` and `ca-central-1` are not fully supported and says to
use `ap-southeast-2`. `lab9_prepare.sh` checks the endpoint table and warns for
those two specifically.

---

## Cleanup

The lab sheet asks you to empty and delete the bucket from the AWS console. The
script does the same thing if you would rather, or to tidy up afterwards:

```bash
python3 lab9_cleanup.py          # empty and delete the bucket
python3 lab9_cleanup.py --empty  # keep the bucket, remove the objects
```

The bucket is the only thing to delete. Every Comprehend and Rekognition call in
this lab is synchronous and stateless - `detect_sentiment`, `detect_labels` and
the rest analyse the input and return, leaving no job, endpoint, collection or
model behind. Asynchronous Comprehend jobs and Rekognition face collections do
persist, but this lab uses neither.
