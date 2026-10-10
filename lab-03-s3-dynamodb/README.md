# Lab 3 - S3 backup/restore and DynamoDB Local

Backs a small local directory tree up to S3 with the same folder structure,
restores it from the bucket, then records each stored file's metadata in a
DynamoDB table running locally in Docker.

- `cloudstorage.py` creates the bucket (with the `LocationConstraint` every
  region except us-east-1 needs) and walks `rootdir/` with `os.walk`, building
  each object key relative to the tree's parent so `rootdir/subdir/...`
  survives the upload.
- `restorefromcloud.py` pages through the bucket and rebuilds the tree under
  `restored/` (or any `--dest`).
- `dynamodb_local.sh` runs `amazon/dynamodb-local` in Docker (in-memory, with
  port-clash detection) instead of the JRE tarball route.
- `write_to_dynamodb.py` creates a `CloudFiles` table (`userId` partition key,
  `fileName` sort key) and writes one item per object: owner, path,
  last-modified time and the ACL permissions read from S3.
- `lab3_scan.sh` and `lab3_delete_table.sh` are the AWS CLI scan and
  delete-table calls against the local endpoint. `lab3_verify.sh` checks it
  all without changing anything.

## AWS services

S3, DynamoDB (Local, via the AWS CLI and boto3), STS.

## Configuration

Required in `lab.env`: `LAB_PREFIX`, `AWS_REGION`. `lab3_prepare.sh` adds
`BUCKET_NAME` (`<prefix>-cloudstorage`). Environment overrides: `DDB_PORT`
(default 8000), `DDB_ENDPOINT`, `DDB_TABLE` (`CloudFiles`), `DDB_CONTAINER`,
`DDB_IMAGE`.

## Run

The Python scripts work on `./rootdir`, which `lab3_prepare.sh` creates under
`~/cits5503/lab3`, so copy this folder's scripts there and run them from there:

```bash
mkdir -p ~/cits5503/lab3 && cp *.py *.sh ~/cits5503/lab3/ && cd ~/cits5503/lab3
./lab3_prepare.sh                 # local tree, venv check, BUCKET_NAME
labenv
python3 cloudstorage.py           # create bucket, upload
python3 restorefromcloud.py       # restore into ./restored
./dynamodb_local.sh start         # needs Docker
python3 write_to_dynamodb.py
./lab3_scan.sh
./lab3_verify.sh
./lab3_delete_table.sh
./dynamodb_local.sh stop
```

## Cost and cleanup

The S3 bucket costs cents but keeps existing until you remove it: empty it,
then delete it. DynamoDB Local runs on your machine and is free. Lab 4 reuses
the same bucket name, so you can leave it until after Lab 4.

See [NOTES.md](NOTES.md) for gotchas I hit along the way.
