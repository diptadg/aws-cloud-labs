#!/usr/bin/env python3
"""
CITS5503 Lab 7 - configure Fabric's SSH entry
----------------------------------------------
Writes the block the lab sheet asks for into ~/.ssh/config:

    Host <your EC2 instance name>
        Hostname <your EC2 instance public IPv4 DNS>
        User ubuntu
        UserKnownHostsFile /dev/null
        StrictHostKeyChecking no
        PasswordAuthentication no
        IdentityFile <path to your private key>

The Host alias is the instance name already in lab.env (LAB7_VM_NAME), so
Connection('<prefix>-vm1') resolves without any host, user or key arguments -
which is what the lab's example code does.

The address is re-read from EC2 rather than trusted from lab.env, because a
stopped and restarted instance comes back on a different public IP and a stale
Hostname here is the single most common reason a Fabric run hangs. LAB7_VM_IP
and LAB7_VM_DNS are refreshed to match.

Finally it runs the smoke test from the lab sheet:

    from fabric import Connection
    c = Connection('<your EC2 instance name>')
    result = c.run('uname -s')      ->  Linux

Run with the shared venv active:
    labenv
    python3 lab7_sshconfig.py
"""

import os
import stat
import sys
import time
from pathlib import Path

try:
    import boto3
except ImportError:
    sys.exit("boto3 not found. Activate the shared lab venv first:  labenv")

try:
    from fabric import Connection
except ImportError:
    sys.exit("fabric not found. Run ./lab7_prepare.sh first.")

from lab7_env import load_env, require, optional, update_env, report

BEGIN = "# --- BEGIN CITS5503 Lab 7 (managed by lab7_sshconfig.py) ---"
END = "# --- END CITS5503 Lab 7 ---"

cfg = load_env()
LAB_PREFIX, AWS_REGION = require(cfg, "LAB_PREFIX", "AWS_REGION")
VM_NAME, VM_ID = require(cfg, "LAB7_VM_NAME", "LAB7_VM_ID")

SSH_USER = optional(cfg, "SSH_USER", "ubuntu")
# KEY_PATH is required, not defaulted: lab7_ec2.py always records it, so an
# empty value here means that script has not run rather than that a guess is wanted.
KEY_PATH = Path(require(cfg, "KEY_PATH")).expanduser()
SSH_CONFIG = Path(optional(cfg, "LAB7_SSH_CONFIG", str(Path.home() / ".ssh" / "config"))).expanduser()


def current_address() -> tuple:
    """Ask EC2 for the instance's public DNS name and IPv4 address right now."""
    ec2 = boto3.client("ec2", region_name=AWS_REGION)
    inst = ec2.describe_instances(InstanceIds=[VM_ID])["Reservations"][0]["Instances"][0]
    state = inst["State"]["Name"]
    if state != "running":
        sys.exit(
            f"{VM_NAME} ({VM_ID}) is '{state}', not running.\n"
            "Start it with: python3 lab7_ec2.py"
        )
    dns = inst.get("PublicDnsName", "")
    ip = inst.get("PublicIpAddress", "")
    if not ip:
        sys.exit(f"{VM_ID} has no public IPv4 address. Checkpoint 2 needs one.")
    return dns or ip, ip


def write_block(hostname: str) -> None:
    """Replace the managed block in ~/.ssh/config, leaving other entries alone."""
    block = "\n".join([
        BEGIN,
        f"Host {VM_NAME}",
        f"    Hostname {hostname}",
        f"    User {SSH_USER}",
        "    UserKnownHostsFile /dev/null",
        "    StrictHostKeyChecking no",
        "    PasswordAuthentication no",
        f"    IdentityFile {KEY_PATH}",
        END,
    ])

    SSH_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    try:
        SSH_CONFIG.parent.chmod(0o700)
    except OSError:
        pass

    existing = SSH_CONFIG.read_text() if SSH_CONFIG.exists() else ""
    existing = existing.replace("\r\n", "\n")  # tolerate a file edited on Windows

    if BEGIN in existing and END in existing:
        head = existing.split(BEGIN, 1)[0]
        tail = existing.split(END, 1)[1]
        text = head.rstrip("\n") + ("\n\n" if head.strip() else "") + block + tail
        action = "updated"
    else:
        if existing.strip():
            text = existing.rstrip("\n") + "\n\n" + block + "\n"
        else:
            text = block + "\n"
        action = "added"

    SSH_CONFIG.write_text(text.rstrip("\n") + "\n")
    SSH_CONFIG.chmod(0o600)

    mode = stat.S_IMODE(SSH_CONFIG.stat().st_mode)
    print(f"{action} the Host block in {SSH_CONFIG} (mode {mode:o})")
    print()
    for line in block.splitlines():
        print(f"    {line}")
    print()


def check_key() -> None:
    if not KEY_PATH.exists():
        sys.exit(f"{KEY_PATH} does not exist. Run: python3 lab7_ec2.py")
    if str(KEY_PATH).startswith("/mnt/") and os.sep == "/":
        sys.exit(
            f"{KEY_PATH} is on the Windows drive. chmod is a no-op on DrvFs and\n"
            "ssh will reject the key as too open. Copy it under ~/.ssh, chmod 600,\n"
            "and update KEY_PATH in lab.env."
        )
    KEY_PATH.chmod(0o600)


def smoke_test() -> None:
    """The lab sheet's own three lines, with a retry while sshd finishes booting."""
    print(f"Connecting:  Connection('{VM_NAME}')")
    last = None
    for attempt in range(1, 21):
        try:
            c = Connection(VM_NAME, connect_timeout=15)
            result = c.run("uname -s", hide=True)
            print()
            print("    from fabric import Connection")
            print(f"    c = Connection('{VM_NAME}')")
            print("    result = c.run('uname -s')")
            print()
            print("    ###Outcome###")
            print(f"    {result.stdout.strip()}")
            print()
            c.close()
            return
        except Exception as exc:  # noqa: BLE001 - report whatever paramiko raised
            last = exc
            print(f"  attempt {attempt}/20 not ready yet ({type(exc).__name__}); waiting 10s")
            time.sleep(10)

    sys.exit(
        f"\nCould not connect to {VM_NAME} after 20 attempts.\nLast error: {last}\n\n"
        "Things worth checking, in order:\n"
        f"  1. TCP 22 outbound from this network  (UWA wifi blocks it)\n"
        f"  2. The security group allows 22 inbound\n"
        f"  3. {KEY_PATH} is mode 600 and on the Linux filesystem\n"
        f"  4. Hostname in {SSH_CONFIG} matches the instance's current address"
    )


def main() -> None:
    report({
        "LAB_PREFIX": LAB_PREFIX,
        "AWS_REGION": AWS_REGION,
        "Instance name": VM_NAME,
        "Instance id": VM_ID,
        "SSH user": SSH_USER,
        "Identity file": str(KEY_PATH),
        "SSH config": str(SSH_CONFIG),
    })

    check_key()
    hostname, ip = current_address()
    print(f"Current public address: {hostname}  ({ip})\n")

    write_block(hostname)
    update_env({
        "LAB7_VM_DNS": hostname if hostname != ip else "",
        "LAB7_VM_IP": ip,
        "LAB7_SSH_CONFIG": str(SSH_CONFIG),
    })

    smoke_test()
    print("Next:  python3 lab7_fabric.py")


if __name__ == "__main__":
    main()
