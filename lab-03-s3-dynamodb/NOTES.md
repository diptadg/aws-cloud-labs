# CITS5503 Lab 3 — S3 & DynamoDB

> Working notes written while doing this lab, aimed at the in-lab demo and its
> checkpoints. Identifiers are placeholders (`<prefix>` = `LAB_PREFIX`), and paths
> assume the scripts were copied into `~/cits5503/labN/`. Start with
> [README.md](README.md) for the overview.

Prefix `<prefix>` · Region `ap-southeast-2` · Bucket `<prefix>-cloudstorage`

## Files

| File | What it does |
|---|---|
| `lab3_prepare.sh` | Builds `rootdir/` + `subdir/`, checks the venv, records `BUCKET_NAME` in `lab.env` |
| `cloudstorage.py` | Creates the S3 bucket and uploads `rootdir/` preserving path structure |
| `restorefromcloud.py` | Reads the bucket and rebuilds the directory tree locally |
| `dynamodb_local.sh` | `start` / `stop` / `status` / `logs` for DynamoDB Local in Docker |
| `write_to_dynamodb.py` | Creates the `CloudFiles` table and writes one item per S3 object |
| `lab3_scan.sh` | The AWS CLI scan command (step 5 / Checkpoint 2) |
| `lab3_delete_table.sh` | The AWS CLI delete-table command (step 6) |
| `lab3_verify.sh` | Read-only rehearsal — checks every checkpoint requirement, changes nothing |
| `../tools/lab_fixperms.sh` | Repairs root-owned files under `~/cits5503` (venv, `rootdir/`, `restored/`, DynamoDB db file) |

## Run order

```bash
mkdir -p ~/cits5503/lab3
# copy these files there, then:
cd ~/cits5503/lab3
chmod +x *.sh

./lab3_prepare.sh              # local tree + venv + lab.env      (no sudo)
labenv                         # activate the shared venv

python3 cloudstorage.py        # [2] create bucket, upload rootdir/
python3 restorefromcloud.py    # [3] restore into ./restored/

./dynamodb_local.sh start      # [4] DynamoDB Local on :8000
python3 write_to_dynamodb.py   # [4] create CloudFiles, write 2 items

../tools/lab_fixperms.sh       # only if anything went root-owned
./lab3_verify.sh               # rehearse everything before the demo
./lab3_scan.sh                 # [5] live scan, table output
./lab3_delete_table.sh         # [6] delete the table
```

Then empty and delete the bucket in the **AWS Management Console**.

## Checkpoint mapping

**Checkpoint 1 — S3 upload and traverse.** Open `cloudstorage.py` and show the
block under the comment `CHECKPOINT 1` — that's the `os.walk(ROOT_DIR)` loop
that visits `rootdir` and everything under it. Then in the Console → S3 →
`<prefix>-cloudstorage`, expand the objects to show:

```
rootdir/
├── rootfile.txt
└── subdir/
    └── subfile.txt
```

**Checkpoint 2 — DynamoDB Local table.** Leave the `dynamodb-local` container
running and run `./lab3_scan.sh`. It must be live, and must return two records
carrying at least `userId`, `fileName`, `path`, `lastUpdated`.

**Checkpoint 3 — Cleanup.** Console → S3 → Buckets → select
`<prefix>-cloudstorage` → **Empty** first, then **Delete**. S3 refuses to delete
a non-empty bucket, so the empty step is not optional. DynamoDB Local runs on
your laptop and isn't part of the cleanup check.

## Configuration

Every script here reads `~/cits5503/lab.env`. Two keys are required and are
**read only** — they belong to the Lab 1 setup script, and duplicating them
across labs is how they drift apart:

```bash
LAB_PREFIX=<prefix>
AWS_REGION=ap-southeast-2
```

If either is missing, the scripts stop with a message pointing back at Lab 1
rather than falling back to a baked-in default.

`BUCKET_NAME` is Lab 3's own key, so `lab3_prepare.sh` **does** write it:

```bash
BUCKET_NAME=<prefix>-cloudstorage
```

It's set to `${LAB_PREFIX}-cloudstorage` and updated in place on re-runs.
The other scripts read it from `lab.env`, falling back to the same derived value
if `lab3_prepare.sh` hasn't run yet, so they work standalone either way.

The only region literal left in the code is the list of AWS regions that return
`DisplayName` from `get_object_acl` (in `write_to_dynamodb.py`) — that's a fact
about AWS, not configuration.

### Overrides

Nothing else is baked in. These env vars change behaviour without editing code:

| Variable | Default | Affects |
|---|---|---|
| `DDB_PORT` | `8000` | `dynamodb_local.sh`, `lab3_scan.sh`, `lab3_delete_table.sh`, `lab3_verify.sh`, `write_to_dynamodb.py` |
| `DDB_ENDPOINT` | `http://localhost:$DDB_PORT` | `write_to_dynamodb.py` only (full URL override) |
| `DDB_TABLE` | `CloudFiles` | the scan, delete, verify and write scripts |
| `DDB_CONTAINER` | `dynamodb-local` | `dynamodb_local.sh` |
| `DDB_IMAGE` | `amazon/dynamodb-local` | `dynamodb_local.sh` |

So a port clash is a one-word fix that moves everything together:

```bash
export DDB_PORT=8001
./dynamodb_local.sh start && python3 write_to_dynamodb.py && ./lab3_scan.sh
```

The values that *are* fixed are fixed because the lab specifies them: the table
name `CloudFiles`, its `userId`/`fileName` key schema, the `rootdir/subdir`
layout, the `1\n2\n3\n4\n5\n` file contents, and the
`<prefix>-cloudstorage` bucket format. The region list in
`write_to_dynamodb.py` is also fixed, but it's AWS's list of regions that return
`DisplayName` from `get_object_acl` — a fact, not a setting.

## Notes and gotchas for this setup

**DynamoDB Local via Docker, not the JRE tarball.** Docker Desktop already
supplies the CLI through WSL integration, so `sudo apt-get install default-jre`
plus the tarball download is unnecessary work. `dynamodb_local.sh` runs
`amazon/dynamodb-local` detached, which also avoids the lab sheet's "don't close
this window" problem.

**`-inMemory` means the data lives only as long as the container.** If you
`docker stop` or restart Docker Desktop between writing the items and the scan,
the table is gone and you'll need to re-run `write_to_dynamodb.py`. Start the
container once and leave it alone until after the scan.

**Bucket names are globally unique.** `<prefix>-cloudstorage` should be free,
but if you ever see `BucketAlreadyExists` it means a different AWS account holds
that name — `BucketAlreadyOwnedByYou` is the harmless one, and the script treats
them differently on purpose.

**`LocationConstraint` is required in `ap-southeast-2`.** Only `us-east-1` is
created without it. The script branches on region so it stays portable.

**`owner` uses the ACL DisplayName.** Sydney is one of the regions where
`get_object_acl` returns `DisplayName`, so `owner` gets that value. The script
falls back to the canonical `ID` if it ever comes back empty.

**If ACLs are disabled on the bucket** (Object Ownership = BucketOwnerEnforced,
the default for new buckets), `get_object_acl` still returns the bucket owner and
a `FULL_CONTROL` grant, so `owner` and `permissions` still populate.

**Port 8000 collisions.** If something on Windows already holds 8000, run
`DDB_PORT=8001 ./dynamodb_local.sh start` and export `DDB_ENDPOINT=http://localhost:8001`
before the Python script; `lab3_scan.sh` and `lab3_verify.sh` honour `DDB_PORT` too.

**Never run these with `sudo`.** It leaves `~/cits5503` root-owned and breaks the
venv — `pip` fails inside it and Python can't write into `restored/`. Recovery:

```bash
../tools/lab_fixperms.sh                            # does all of the below
# or by hand:
sudo chown -R $USER:$USER ~/cits5503                # everything
sudo chown -R $USER:$USER ~/cits5503/.venv          # venv specifically
sudo chown -R $USER:$USER ~/cits5503/lab3/rootdir
sudo chown -R $USER:$USER ~/cits5503/lab3/restored
chmod +x ~/cits5503/lab3/*.sh                       # exec bits after the chown
```

`lab3_prepare.sh` also self-heals: it scans for non-`$USER` files under
`~/cits5503` on startup and chowns them back before doing anything else, and
`lab3_verify.sh` flags them during the rehearsal.

**Container-written files need a chown too.** This only bites if you swap
`-inMemory` for `-dbPath` and bind-mount a host directory — the container writes
`shared-local-instance.db` as its own UID, so you get a root-owned file in your
lab folder:

```bash
sudo chown $USER:$USER ~/cits5503/lab3/dynamodb/shared-local-instance.db
# or: ./dynamodb_local.sh fixperms
```

The default `-inMemory` setup here writes nothing to disk, so there's normally
nothing to fix.

**`~/.aws` is the exception.** It's symlinked to `/mnt/c/Users/<you>/.aws`, and
`chown`/`chmod` are silent no-ops on DrvFs. Don't chase permission warnings
there — the AWS CLI works regardless.

**Clock drift after Windows sleep** produces signature-expired errors from AWS.
Fix with `sudo hwclock -s`.

**PEP 668.** Ubuntu 26.04 blocks `pip3 install` outside a venv; everything here
uses the shared `~/cits5503/.venv` via `labenv`.
