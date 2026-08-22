"""Inspect and validate bundled V8 cached-data profiles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .header import parse_header
from .profiles import load_profiles, read_profile_data


def _corpus_files(corpus: str | Path) -> list[Path]:
    root = Path(corpus).expanduser().resolve()
    if root.is_file():
        return [root]
    if not root.is_dir():
        raise ValueError(f"corpus does not exist: {root}")
    suffixes = {".jsc", ".v8blob", ".bin"}
    return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in suffixes)


def coverage_report(corpus: str | Path, profile_directory: str | None = None) -> dict[str, object]:
    profiles = load_profiles(profile_directory)
    files: list[dict[str, object]] = []
    for path in _corpus_files(corpus):
        try:
            header, profile = parse_header(path.read_bytes(), profiles)
            files.append({
                "path": str(path),
                "success": True,
                "profile": profile.version,
                "version_hash": f"0x{header.version_hash:08x}",
                "header_format": header.format,
                "header_size": header.header_size,
                "payload_length": header.payload_length,
            })
        except (OSError, ValueError) as exc:
            files.append({"path": str(path), "success": False, "error": str(exc)})
    by_profile: dict[str, int] = {}
    for item in files:
        if item.get("success"):
            profile = str(item["profile"])
            by_profile[profile] = by_profile.get(profile, 0) + 1
    return {
        "format": 1,
        "corpus": str(Path(corpus).expanduser().resolve()),
        "profile_directory": str(profiles.directory),
        "profile_count": len(profiles.profiles),
        "file_count": len(files),
        "empty": not files,
        "matched": sum(1 for item in files if item.get("success")),
        "failed": sum(1 for item in files if not item.get("success")),
        "profiles": by_profile,
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="cached_data.profile_cli")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("list", "list V8 profiles"),
        ("validate", "validate profile schema and invariants"),
        ("coverage", "match a local .jsc/.v8blob corpus against exact profiles"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "--profile-dir",
            help="directory containing an exact profile pack with index.json",
        )
        if name == "coverage":
            command.add_argument("--corpus", required=True)
            command.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        if args.command == "list":
            profiles = load_profiles(args.profile_dir)
            for profile in profiles.profiles:
                print(f"{profile.version}\t0x{profile.version_hash:08x}")
            return 0

        if args.command == "coverage":
            report = coverage_report(args.corpus, args.profile_dir)
            if args.json:
                print(json.dumps(report, indent=2, ensure_ascii=False))
            else:
                if report["empty"]:
                    print(f"Coverage: no .jsc/.v8blob files found in {report['corpus']}")
                else:
                    print(
                        f"Coverage: {report['matched']}/{report['file_count']} matched; "
                        f"{report['failed']} failed against {report['profile_count']} profiles."
                    )
                for item in report["files"]:
                    status = "OK" if item["success"] else "FAILED"
                    detail = item.get("profile", item.get("error", ""))
                    print(f"{status}\t{item['path']}\t{detail}")
            return 0 if report["file_count"] > 0 and report["failed"] == 0 else 2

        index, items = read_profile_data(args.profile_dir)
        print(f"Validated {len(items)} V8 profiles (schema format {index['format']}).")
        return 0
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
