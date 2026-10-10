"""CITS5503 Lab 8 - shared access to ~/cits5503/lab.env for the Python scripts.

lab.env is the single source of truth for every configurable value in the lab
suite. Nothing here invents an identity value: if LAB_PREFIX or AWS_REGION
is missing, the calling script stops and names the key rather than falling back
to a baked-in default.

Keys that Labs 1-7 already recorded (LAB_PREFIX, AWS_REGION,
AWS_ACCOUNT_ID) are read as-is. Lab 8 never writes a second copy of an existing
key under a new name - see set_if_absent().

Override the file location with LAB_ENV=/some/other/path.
"""

import os
import sys
from pathlib import Path

ENV_FILE = Path(os.environ.get("LAB_ENV", Path.home() / "cits5503" / "lab.env"))


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return value


def load_env() -> dict:
    """Parse KEY=VALUE / export KEY="VALUE" lines out of lab.env."""
    if not ENV_FILE.exists():
        sys.exit(
            f"{ENV_FILE} not found.\n"
            "Lab 1 creates this file. Run the Lab 1 setup before Lab 8."
        )

    cfg = {}
    for raw in ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        cfg[key.strip()] = _unquote(val)
    return cfg


def require(cfg: dict, *keys):
    """Return the named values, or exit naming the first key that is missing."""
    out = []
    for key in keys:
        val = cfg.get(key, "").strip()
        if not val:
            sys.exit(
                f"{key} is missing or empty in {ENV_FILE}.\n"
                "Add it there rather than hard-coding it in a script."
            )
        out.append(val)
    return out[0] if len(out) == 1 else out


def optional(cfg: dict, key: str, default: str) -> str:
    """A value with a documented, non-identity default (ports, role names).

    Precedence: process environment, then lab.env, then the default. The
    environment override exists so a one-off run can change a value without
    editing lab.env.
    """
    return (os.environ.get(key) or cfg.get(key, "") or default).strip()


def update_env(updates: dict) -> None:
    """Insert or replace KEY="value" lines in lab.env, preserving file order."""
    if not updates:
        return

    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    remaining = dict(updates)

    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        body = stripped[len("export "):].strip() if stripped.startswith("export ") else stripped
        key = body.split("=", 1)[0].strip()
        if key in remaining:
            prefix = "export " if stripped.startswith("export ") else ""
            lines[i] = f'{prefix}{key}="{remaining.pop(key)}"'

    if remaining:
        header = "# --- Lab 8 ---"
        if not any(line.strip() == header for line in lines):
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(header)
        for key, val in remaining.items():
            lines.append(f'{key}="{val}"')

    ENV_FILE.write_text("\n".join(lines).rstrip("\n") + "\n")
    try:
        ENV_FILE.chmod(0o600)
    except OSError:
        pass  # no-op on DrvFs if lab.env ever lives under /mnt/c


def set_if_absent(cfg: dict, updates: dict) -> dict:
    """Write only the keys an earlier lab has not already recorded.

    Keeps Lab 8 from shadowing AWS_ACCOUNT_ID (or anything else Labs 1-7 own)
    with a second copy under a new name. Returns what was actually written.
    """
    new = {k: v for k, v in updates.items() if not cfg.get(k, "").strip()}
    update_env(new)
    cfg.update(new)
    return new


def report(values: dict) -> None:
    """Print the resolved configuration so a wrong value is visible immediately."""
    width = max(len(k) for k in values)
    print(f"Configuration from {ENV_FILE}")
    for key, val in values.items():
        print(f"  {key.ljust(width)}  {val}")
    print()
