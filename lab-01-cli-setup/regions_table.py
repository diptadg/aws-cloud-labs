#!/usr/bin/env python3
"""
CITS5503 Lab 1 - Checkpoint 3

Retrieves region data through boto3 and prints a table with exactly two
columns: Endpoint and RegionName.

Usage:
    python3 regions_table.py           # enabled regions only (default)
    python3 regions_table.py --all     # include opted-out / disabled regions
"""

import sys

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
except ImportError:
    sys.exit(
        "boto3 is not available.\n"
        "Activate the lab virtual environment first:\n"
        "    source ~/cits5503/.venv/bin/activate"
    )

HEADERS = ("Endpoint", "RegionName")


def fetch_regions(all_regions: bool = False):
    """Call EC2 DescribeRegions and return a list of (Endpoint, RegionName)."""
    ec2 = boto3.client("ec2")
    response = ec2.describe_regions(AllRegions=all_regions)
    rows = [(r["Endpoint"], r["RegionName"]) for r in response["Regions"]]
    return sorted(rows, key=lambda row: row[1])


def render_table(rows, headers=HEADERS) -> str:
    """Render rows as a bordered ASCII table, mirroring `aws --output table`."""
    columns = zip(headers, *rows) if rows else ((h,) for h in headers)
    widths = [max(len(str(cell)) for cell in column) for column in columns]

    divider = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    def line(cells):
        padded = (f" {str(c).ljust(w)} " for c, w in zip(cells, widths))
        return "|" + "|".join(padded) + "|"

    out = [divider, line(headers), divider]
    out.extend(line(r) for r in rows)
    out.append(divider)
    return "\n".join(out)


def main() -> int:
    all_regions = "--all" in sys.argv[1:]

    try:
        rows = fetch_regions(all_regions)
    except NoCredentialsError:
        print("No AWS credentials found. Run: aws configure", file=sys.stderr)
        return 1
    except (BotoCoreError, ClientError) as exc:
        print(f"AWS call failed: {exc}", file=sys.stderr)
        print(
            "If this mentions an expired or invalid signature, your WSL clock "
            "has drifted — run: sudo hwclock -s",
            file=sys.stderr,
        )
        return 1

    print(render_table(rows))
    print(f"\n{len(rows)} regions returned via boto3 describe_regions().")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
