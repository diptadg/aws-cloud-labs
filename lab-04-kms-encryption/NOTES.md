# CITS5503 Lab 4 — KMS & Encryption

> Working notes written while doing this lab, aimed at the in-lab demo and its
> checkpoints. Identifiers are placeholders (`<prefix>` = `LAB_PREFIX`), and paths
> assume the scripts were copied into `~/cits5503/labN/`. Start with
> [README.md](README.md) for the overview.

Prefix `<prefix>` · Region `ap-southeast-2` · Bucket `<prefix>-cloudstorage` · KMS alias `alias/<prefix>`

## Files

| File | What it does |
|---|---|
| `lab4_prepare.sh` | Venv + `boto3`/`pycryptodome`, rebuilds `rootdir/`, recreates the bucket if Lab 3 deleted it, records Lab 4 vars in `lab.env` |
| `bucket_policy.py` | Applies the deny-everyone-but-me bucket policy (`--show`, `--remove` also available) |
| `lab4_check_policy.sh` | Runs the Checkpoint 1 `get-bucket-policy` command and sanity-reads the result |
| `kms_key.py` | Creates the KMS key, aliases it to your prefix, attaches the key policy (`--show` prints it back) |
| `kms_encrypt.py` | Encrypts/decrypts every bucket file with the KMS key, uploads the results |
| `fileencrypt.py` | Same job with `pycryptodome` AES-256-CBC — this is the script to open in Checkpoint 3 |
| `lab4_verify.sh` | Read-only rehearsal of all four checkpoints; changes nothing |

## Run order

```bash
mkdir -p ~/cits5503/lab4
# copy these files there, then:
cd ~/cits5503/lab4
sudo chown -R "$USER:$USER" ~/cits5503/lab4   # only if anything landed root-owned
chmod +x *.sh

./lab4_prepare.sh              # venv, deps, rootdir/, bucket        (no sudo)
labenv                         # activate the shared venv

python3 bucket_policy.py       # apply the restrictive bucket policy
./lab4_check_policy.sh         # read it back with the CLI

python3 kms_key.py             # create key + alias + key policy
python3 kms_encrypt.py         # KMS encrypt/decrypt, then upload
python3 fileencrypt.py         # pycryptodome encrypt/decrypt, then upload

./lab4_verify.sh               # rehearse everything before the demo
```

Then do the cleanup in the **AWS Management Console**.

Everything is idempotent — an existing bucket, key or alias is reused rather
than duplicated, so re-running any script is safe.

## Configuration lives only in `lab.env`

No script hardcodes a prefix, region, bucket name or alias. Every value
is read from `~/cits5503/lab.env`, and a script that cannot find what it needs
stops with a message naming the missing key rather than falling back to a
guess. A stale `lab.env` therefore fails loudly instead of quietly writing a
policy for the wrong bucket.

| Key | Written by | Read by |
|---|---|---|
| `LAB_PREFIX`, `AWS_REGION` | Lab 1 — must already be there | all |
| `BUCKET_NAME`, `KMS_ALIAS`, `ROOT_DIR` | `lab4_prepare.sh` (derived if absent) | all |
| `IAM_USERNAME`, `ACCOUNT_ID` | `lab4_prepare.sh`, from `sts get-caller-identity` | `bucket_policy.py`, `kms_key.py`, `lab4_verify.sh` |
| `KMS_KEY_ID`, `KMS_KEY_ARN` | `kms_key.py`, recording the key it created | `lab4_verify.sh` |

`lab4_prepare.sh` is the only script that writes configuration; `kms_key.py`
only records the key it just made. If `LAB_PREFIX` or `AWS_REGION` are
missing, even `lab4_prepare.sh` refuses to guess them — add them to `lab.env`
by hand and re-run it.

Both `bucket_policy.py` and `kms_key.py` compare `IAM_USERNAME`/`ACCOUNT_ID`
against the live `sts get-caller-identity` before building a policy, and abort
on a mismatch. Without that check, a `lab.env` carried over from a different
account would produce a bucket policy that denies you access to your own
bucket.

Every AWS CLI call also passes `--region "${AWS_REGION}"` explicitly rather
than leaning on whatever `aws configure` happens to hold, so `lab.env` is the
single source of truth for the region too. The exceptions are
`sts get-caller-identity`, which is region-agnostic, and the
`aws configure get region` check in `lab4_verify.sh`, which is deliberately
reading the CLI default in order to compare it.

What remains literal on purpose: the `subdir/rootfile.txt/subfile.txt` names
and the `1..5` file contents (fixed by the Lab 3 specification), the
`.aes.enc` / `.aes.dec` / `.kms.enc` / `.kms.dec` suffixes, the `~/cits5503`
paths, AES-256 and the 16-byte IV, KMS's 4 KB plaintext ceiling, and the
`default` KMS policy name. None of those vary per user.

## Checkpoint mapping

**Checkpoint 1 — Bucket policy.** Run `./lab4_check_policy.sh`, or just the
command itself:

```bash
aws s3api get-bucket-policy --bucket <prefix>-cloudstorage --query Policy --output text
```

Then in the S3 console, signed in as yourself, open
`<prefix>-cloudstorage/rootdir` and show that you can read it. A facilitator
then opens the same folder with their own AWS account and gets `AccessDenied`.

The policy differs from the one printed in the lab sheet in one way that
matters: `Resource` lists both `arn:aws:s3:::<prefix>-cloudstorage` and
`arn:aws:s3:::<prefix>-cloudstorage/*`. The sheet's example only names an
object path, and with that alone `s3:ListBucket` is never denied — a
facilitator would still see the object listing, which is what the checkpoint is
testing.

**Checkpoint 2 — KMS configuration.** Console → KMS → Customer managed keys →
select the key with alias `<prefix>` → **Key policy** tab. Point at the
`Allow access for Key Administrators` and `Allow use of the key` statements;
both name `arn:aws:iam::<account-id>:user/<iam-user>`.
`python3 kms_key.py --show` prints the same policy in the terminal if you want
to check it first.

**Checkpoint 3 — pycryptodome.** Open `fileencrypt.py` at the block marked
`CHECKPOINT 3` — `encrypt_file()` and `decrypt_file()`, plus the `main()` loop
that applies them to each file in the bucket. Run `python3 fileencrypt.py`
live until it finishes, then in the console show the three kinds of object:

```
rootdir/rootfile.txt              (original)
rootdir/rootfile.txt.aes.enc      (encrypted)
rootdir/rootfile.txt.aes.dec      (decrypted)
rootdir/subdir/subfile.txt        + the same two
```

**Checkpoint 4 — Cleanup.** Console only, in this order:

1. S3 → `<prefix>-cloudstorage` → **Empty**, then **Delete**. A bucket has to
   be empty before it can be deleted, and by now it holds a dozen objects.
2. KMS → Customer managed keys → select the key → **Key actions → Schedule key
   deletion**. KMS will not delete a key immediately; 7 days is the minimum
   waiting period, so schedule it for 7 days.

DynamoDB Local runs on your laptop and is outside the cleanup check.

## Things that bite on this setup

**Never run these with `sudo`.** Files end up root-owned and then the venv and
`~/.aws` stop working for your normal user. If it happens:
`sudo chown -R "$USER:$USER" ~/cits5503`.

**The bucket policy also denies the account root user.** Root has no
`aws:username`, so `StringNotLike` matches and the Deny applies. Stay signed in
as your IAM user for the whole lab. `python3 bucket_policy.py --remove` takes
the policy off again if you need it, and every apply backs up the previous
policy to `bucket_policy_backup_<timestamp>.json` first.

**You must be an IAM user, not a role.** `bucket_policy.py` and `kms_key.py`
both refuse to run if `sts get-caller-identity` returns anything other than an
`:user/` ARN, because `aws:username` does not exist for assumed roles and the
policy would silently deny you.

**`pip3 install` is blocked on Ubuntu 26.04** (PEP 668). `pycryptodome` and
`boto3` go into the venv; `lab4_prepare.sh` handles this.

**WSL clock drift after a Windows sleep** produces expired-signature errors on
every AWS call. Fix: `sudo hwclock -s`.

**`~/.aws` is a symlink onto the Windows filesystem,** so `chmod` on the
credentials files is a no-op. Harmless here — nothing in Lab 4 needs `.pem`
permissions.

**Recreating a deleted bucket takes a moment.** S3 bucket names are global and
a just-deleted name can take a few minutes to become available again. If
`lab4_prepare.sh` fails with a bucket-name error, wait and re-run it.

**`kms.encrypt` caps plaintext at 4 KB.** The lab files are 10 bytes, so
`kms_encrypt.py` encrypts them directly; anything larger falls through to
envelope encryption (KMS issues a data key, AES-256 does the bulk locally). A
one-byte header records which path was taken so decryption knows what to do.

**The AES key lives at `~/cits5503/lab4/aes.key`,** generated on the first run
of `fileencrypt.py` and reused afterwards, so a later run can still decrypt an
earlier run's `.aes.enc` files. Delete it and old ciphertext becomes
unrecoverable. That key never leaving your laptop is precisely the contrast
with KMS, where the key material stays inside AWS and every operation costs an
API round trip.
