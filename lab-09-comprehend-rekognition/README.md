# Lab 9 - Comprehend and Rekognition

Exercises the synchronous AWS NLP and computer-vision APIs from the command
line: Comprehend on short texts in four languages, and Rekognition on four
images stored in S3.

- `lab9_comprehend.py` runs language detection, sentiment, entities, key
  phrases and syntax. It prompts for a text by default; `--all-texts`,
  `--text`, `--input` and `--file` are non-interactive. Each text goes through
  `detect_dominant_language` first, and the detected code drives the other
  calls, so any of the sample texts works without being told its language.
  Language support differs per API and is checked before calling, and the
  5,000-byte request limit is checked up front.
- `lab9_languages.py` maps language codes to English names (Babel or
  pycountry if installed, otherwise a built-in table), and reports confidence
  truncated rather than rounded, so 0.996 never prints as 100%.
- `lab9_bucket.py` validates the four images offline (JPEG/PNG magic bytes,
  dimensions parsed from the header without Pillow, size limits), then
  creates the bucket and uploads them with their real `Content-Type`.
- `lab9_rekognition.py` runs one analysis per image, all reading from S3 by
  reference: labels (with bounding boxes), moderation labels, faces with the
  full attribute set, and text lines.
- `lab9_verify.sh` checks detected languages against the expected ones, drives
  the prompt path end to end on stdin, and checks the bucket's region and every
  image analysis. `lab9_cleanup.py` empties and deletes the bucket.

## AWS services

Comprehend, Rekognition, S3, STS.

## Configuration

Required in `lab.env`: `LAB_PREFIX`, `AWS_REGION` (Comprehend and Rekognition
are not available in every region; `ap-southeast-2` works).
`lab9_prepare.sh` writes `LAB9_BUCKET`, `LAB9_IMAGE_DIR`, `LAB9_IMAGE_*`,
`LAB9_MIN_CONFIDENCE` (default 80) and `LAB9_MAX_LABELS` (default 20).

## Images

The images are not included, because the ones I used were third-party
photos. Put four JPEG or PNG files of your own in `images/`, named
`urban`, `beach`, `faces` and `text` (any of `.jpg`, `.jpeg`, `.png`): a street
scene, a beach scene, people's faces, and a picture containing text. See
[images/PUT_IMAGES_HERE.txt](images/PUT_IMAGES_HERE.txt). Everything else in
`images/` is git-ignored.

## Run

```bash
labenv
./lab9_prepare.sh
python3 lab9_comprehend.py all --all-texts
python3 lab9_bucket.py --check && python3 lab9_bucket.py
python3 lab9_rekognition.py all
./lab9_verify.sh
python3 lab9_cleanup.py
```

## Cost and cleanup

Comprehend and Rekognition bill per request (the free tier covers a run of
this size); the calls are stateless and leave nothing behind. The S3 bucket is
the only resource: `python3 lab9_cleanup.py` empties and deletes it.

See [NOTES.md](NOTES.md) for gotchas I hit along the way.
