# Lab 4 - Bucket policy, KMS and client-side AES

Locks an S3 bucket down to a single IAM user, then encrypts and decrypts the
bucket's files two ways: with an AWS KMS key, and locally with AES-256 via
pycryptodome.

- `bucket_policy.py` applies a `Deny s3:*` policy to everyone whose
  `aws:username` is not yours. It covers both `arn:aws:s3:::bucket` and
  `bucket/*`, so listing is denied too, not just object reads. It refuses to
  run as an assumed role (no `aws:username`) or when `lab.env` names a
  different user than the live credentials, so it can't lock you out. It backs
  up any existing policy first, and `--remove` takes the policy off.
- `kms_key.py` creates a symmetric KMS key aliased `alias/<prefix>` and
  attaches a key policy naming your IAM user as both key administrator and key
  user.
- `kms_encrypt.py` encrypts each original object with `kms.encrypt` (falling
  back to envelope encryption with a KMS data key above the 4 KB limit),
  decrypts it, checks the round trip and uploads `.kms.enc` / `.kms.dec`.
- `fileencrypt.py` does the same with AES-256-CBC: a random IV per file is
  stored with the ciphertext, and a persistent local key at
  `~/cits5503/lab4/aes.key` (mode 600).

## AWS services

S3 (bucket policy), KMS (keys, aliases, key policy, encrypt/decrypt, data
keys), IAM (identity used in both policies), STS.

## Configuration

Required in `lab.env`: `LAB_PREFIX`, `AWS_REGION`. `lab4_prepare.sh` derives
and writes `BUCKET_NAME`, `KMS_ALIAS`, `ROOT_DIR`, and reads `IAM_USERNAME`
and `ACCOUNT_ID` from STS. `kms_key.py` records `KMS_KEY_ID` and
`KMS_KEY_ARN`. You must be signed in as an IAM **user**, not a role.

## Run

```bash
./lab4_prepare.sh             # deps, local tree, bucket, lab.env keys
labenv
python3 bucket_policy.py      # --show / --remove
./lab4_check_policy.sh
python3 kms_key.py            # --show prints the key policy
python3 kms_encrypt.py        # --no-upload to keep it local
python3 fileencrypt.py
./lab4_verify.sh
```

## Cost and cleanup

**A customer-managed KMS key bills monthly (prorated) for as long as it
exists**, plus a small charge per API call. Schedule its deletion (7-day
minimum waiting period) when you are done. Empty and delete the bucket. The
deny policy also blocks the account root user, so remove it first
(`python3 bucket_policy.py --remove`) if you will clean up as root. Keep
`aes.key` out of version control (it is git-ignored).

See [NOTES.md](NOTES.md) for gotchas I hit along the way.
