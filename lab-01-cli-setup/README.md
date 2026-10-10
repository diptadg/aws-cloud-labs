# Lab 1 - AWS CLI, boto3 and WSL2 setup

Gets a fresh WSL2 Ubuntu install ready for the rest of the labs, then proves
the toolchain works by listing the enabled AWS regions two ways.

`lab1_setup.sh` installs AWS CLI v2 from Amazon's official installer (not the
v1 package apt offers), creates a shared Python virtualenv with boto3 (Ubuntu's
system Python refuses `pip install` under PEP 668), adds a `labenv` alias to
activate it, runs `aws configure` for your access key, and writes the shared
`lab.env` config file that every later lab reads. It also resyncs the WSL2
clock up front, because a drifted clock makes every AWS call fail with a
signature error.

`regions_table.py` calls EC2 `DescribeRegions` through boto3 and prints an
`Endpoint` / `RegionName` table in the same style as `aws --output table`.
`lab1_verify.sh` re-checks everything: CLI version, boto3 import, caller
identity, configured region, and the table's columns.

## AWS services

EC2 (`DescribeRegions` only), STS (`GetCallerIdentity`), IAM access keys.

## Configuration

`lab1_setup.sh` prompts for these and writes them to `~/cits5503/lab.env`
(existing values are offered as defaults):

| Key | Meaning |
|---|---|
| `LAB_PREFIX` | prefix for every resource name in later labs |
| `AWS_REGION` | default `ap-southeast-2` |
| `AWS_AMI` | Ubuntu AMI id in that region, used from Lab 2 on |

Environment overrides: `LAB_DIR` (default `~/cits5503`), `LAB_ENV` (config path).

## Run

```bash
./lab1_setup.sh                 # full setup; --skip-upgrade for faster re-runs
labenv                          # activate the venv (open a new shell first)
python3 regions_table.py        # --all to include opted-out regions
./lab1_verify.sh                # re-checks everything; exits non-zero on failure
```

Run it as your normal user, not with `sudo`: the script calls `sudo` itself
only for apt and the CLI installer.

## Cost

Nothing billable. It only reads account metadata. Treat the access key it
configures as a secret: keep `~/.aws` on the Linux filesystem, and rotate or
delete the key when you are done.
