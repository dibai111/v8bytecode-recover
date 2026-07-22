"""Inspect and validate bundled V8 cached-data profiles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .profiles import load_profiles, validate_profile_data


def _raw_profiles() -> tuple[dict, list[dict]]:
    directory = Path(__file__).with_name("profiles")
    index = json.loads((directory / "index.json").read_text(encoding="utf-8"))
    items = [
        json.loads((directory / f"{version}.json").read_text(encoding="utf-8"))
        for version in index.get("versions", [])
    ]
    return index, items


def main() -> int:
    parser = argparse.ArgumentParser(prog="cached_data.profile_cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list bundled V8 profiles")
    commands.add_parser("validate", help="validate profile schema and invariants")
    args = parser.parse_args()

    if args.command == "list":
        profiles = load_profiles()
        for profile in profiles.profiles:
            print(f"{profile.version}\t0x{profile.version_hash:08x}")
        return 0

    index, items = _raw_profiles()
    errors = validate_profile_data(index, items)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"Validated {len(items)} V8 profiles (schema format {index['format']}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
