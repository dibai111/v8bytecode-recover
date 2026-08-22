#!/usr/bin/env python3
"""Discover exact semantic V8 tags for repeatable profile generation."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


VERSION_RE = re.compile(r"^(\d+(?:\.\d+){2,3})$")


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def read_tags(repository: str) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--tags", "--refs", repository],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("git executable was not found") from exc
    tags: set[str] = set()
    for line in result.stdout.splitlines():
        _, _, ref = line.partition("\t")
        tag = ref.removeprefix("refs/tags/")
        if VERSION_RE.fullmatch(tag):
            tags.add(tag)
    return sorted(tags, key=version_key)


def select_tags(
    tags: list[str],
    minimum: tuple[int, ...],
    maximum: tuple[int, ...] | None,
    per_minor: bool,
    limit: int | None,
) -> list[str]:
    selected = [
        tag for tag in tags
        if version_key(tag) >= minimum
        and (maximum is None or version_key(tag) <= maximum)
    ]
    if per_minor:
        by_minor: dict[tuple[int, int], str] = {}
        for tag in selected:
            parts = version_key(tag)
            by_minor[(parts[0], parts[1])] = tag
        selected = sorted(by_minor.values(), key=version_key)
    if limit is not None:
        selected = selected[:limit]
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover exact V8 tags for profile generation"
    )
    parser.add_argument("--repository", default="https://github.com/v8/v8.git")
    parser.add_argument("--minimum", default="5.1.0")
    parser.add_argument("--maximum")
    parser.add_argument(
        "--all",
        action="store_true",
        help="include every semantic tag instead of the latest tag per minor line",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    tags = read_tags(args.repository)
    selected = select_tags(
        tags,
        version_key(args.minimum),
        version_key(args.maximum) if args.maximum else None,
        per_minor=not args.all,
        limit=args.limit,
    )
    if not selected:
        parser.error("no matching semantic V8 tags were found")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text("\n".join(selected) + "\n", encoding="utf-8")
    else:
        print("\n".join(selected))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {
                    "format": 1,
                    "repository": args.repository,
                    "discovered": len(tags),
                    "selected": len(selected),
                    "selection": "all" if args.all else "latest-per-minor",
                    "versions": selected,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
