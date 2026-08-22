from __future__ import annotations

import struct
import unittest
from dataclasses import replace

try:
    from .compatibility import build_compatibility_report
    from .header import parse_header
    from .profiles import load_profiles
except ImportError:  # unittest discover with cached_data as the top-level path.
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from cached_data.compatibility import build_compatibility_report  # type: ignore[no-redef]
    from cached_data.header import parse_header  # type: ignore[no-redef]
    from cached_data.profiles import load_profiles  # type: ignore[no-redef]


LAYOUTS = {
    "legacy": {
        "base": 24,
        "version": 4,
        "source": 8,
        "flags": 12,
        "payload": 16,
        "checksums": (20,),
    },
    "read-only-checksum": {
        "base": 28,
        "version": 4,
        "source": 8,
        "flags": 12,
        "payload": 20,
        "checksums": (24,),
        "snapshot": 16,
    },
    "reservation-checksum": {
        "base": 28,
        "version": 4,
        "source": 8,
        "flags": 12,
        "payload": 20,
        "checksums": (24,),
        "reservations": 16,
    },
    "reservation-checksum-pair": {
        "base": 32,
        "version": 4,
        "source": 8,
        "flags": 12,
        "payload": 20,
        "checksums": (24, 28),
        "reservations": 16,
    },
    "legacy-code-stub": {
        "base": 40,
        "version": 4,
        "source": 8,
        "flags": 16,
        "payload": 28,
        "checksums": (32, 36),
        "cpu": 12,
        "reservations": 20,
        "stubs": 24,
    },
    "legacy-code-stub-extra": {
        "base": 44,
        "version": 8,
        "source": 12,
        "flags": 20,
        "payload": 32,
        "checksums": (36, 40),
        "cpu": 16,
        "reservations": 28,
        "stubs": 24,
        "external": 4,
    },
}


def fixture_profile(format_name: str, version_hash: int):
    profiles = load_profiles()
    profile = replace(
        profiles.by_version("9.4.146.24"),
        version=f"99.0.{version_hash}",
        version_hash=version_hash,
        header_format=format_name,
        has_ro_snapshot_checksum=format_name == "read-only-checksum",
        cache_header_layout=None,
    )
    return replace(profiles, profiles=(profile,))


def fixture_blob(format_name: str, version_hash: int) -> bytes:
    layout = LAYOUTS[format_name]
    reservation_count = 1 if "reservations" in layout else 0
    stub_count = 1 if "stubs" in layout else 0
    metadata_end = layout["base"] + (reservation_count + stub_count) * 4
    payload_start = (metadata_end + 7) & ~7
    data = bytearray(payload_start + 4)
    struct.pack_into("<I", data, 0, 0xC0DE0001)
    struct.pack_into("<I", data, layout["version"], version_hash)
    struct.pack_into("<I", data, layout["source"], 0x11223344)
    struct.pack_into("<I", data, layout["flags"], 0x55667788)
    if "snapshot" in layout:
        struct.pack_into("<I", data, layout["snapshot"], 0x01020304)
    if "cpu" in layout:
        struct.pack_into("<I", data, layout["cpu"], 0x0A0B0C0D)
    if "external" in layout:
        struct.pack_into("<I", data, layout["external"], 7)
    if "reservations" in layout:
        struct.pack_into("<I", data, layout["reservations"], reservation_count)
    if "stubs" in layout:
        struct.pack_into("<I", data, layout["stubs"], stub_count)
    struct.pack_into("<I", data, layout["payload"], 4)
    for index, offset in enumerate(layout["checksums"]):
        struct.pack_into("<I", data, offset, 0xAABBCCDD + index)
    struct.pack_into("<I", data, payload_start, 0xDEADBEEF)
    return bytes(data)


class HeaderFixtureTests(unittest.TestCase):
    def test_parses_all_historical_header_shapes(self):
        for index, format_name in enumerate(LAYOUTS):
            version_hash = 0x10000000 + index
            profile_set = fixture_profile(format_name, version_hash)
            header, profile = parse_header(fixture_blob(format_name, version_hash), profile_set)

            self.assertEqual(header.format, format_name)
            self.assertEqual(header.version_hash, version_hash)
            self.assertEqual(header.source_hash, 0x11223344)
            self.assertEqual(header.flags_hash, 0x55667788)
            self.assertEqual(header.payload_length, 4)
            self.assertEqual(header.reservation_count, 1 if "reservations" in LAYOUTS[format_name] else 0)
            self.assertEqual(profile.version_hash, version_hash)

    def test_rejects_non_zero_header_padding(self):
        version_hash = 0x20000001
        data = bytearray(fixture_blob("read-only-checksum", version_hash))
        data[29] = 1

        with self.assertRaisesRegex(ValueError, "header padding is not zero"):
            parse_header(bytes(data), fixture_profile("read-only-checksum", version_hash))

    def test_reports_exact_unknown_and_version_only_embedder_matches(self):
        profiles = load_profiles()
        base = profiles.by_version("13.2.152.41")
        exact = replace(
            base,
            version_hash=0x30000001,
            header_format="legacy",
            cache_header_layout=None,
        )
        exact = replace(
            exact,
            runtime_variant_by_flags_hash={0x55667788: "legacy"},
        )
        unknown = replace(
            exact,
            version_hash=0x30000002,
            runtime_variant_by_flags_hash={},
        )
        version_only = replace(
            profiles.by_version("10.2.154.4"),
            version_hash=0x30000003,
            header_format="legacy",
            cache_header_layout=None,
        )

        for profile, expected in (
            (exact, "exact-runtime-variant"),
            (unknown, "unknown-flags"),
            (version_only, "version-only"),
        ):
            header, _ = parse_header(
                fixture_blob("legacy", profile.version_hash),
                replace(profiles, profiles=(profile,)),
            )
            report = build_compatibility_report(header, profile)
            self.assertEqual(report["status"], expected)

    def test_requires_a_matching_read_only_snapshot_for_exact_compatibility(self):
        version_hash = 0x40000001
        profile_set = fixture_profile("read-only-checksum", version_hash)
        header, profile = parse_header(
            fixture_blob("read-only-checksum", version_hash), profile_set
        )

        unverified = build_compatibility_report(header, profile)
        matched = build_compatibility_report(header, profile, snapshot_checksum=0x01020304)
        mismatch = build_compatibility_report(header, profile, snapshot_checksum=0xFFFFFFFF)

        self.assertEqual(unverified["status"], "snapshot-unverified")
        self.assertEqual(unverified["snapshot_status"], "required")
        self.assertEqual(matched["status"], "version-only")
        self.assertEqual(matched["snapshot_status"], "matched")
        self.assertEqual(mismatch["status"], "snapshot-mismatch")
        self.assertEqual(mismatch["snapshot_status"], "mismatch")


if __name__ == "__main__":
    unittest.main()
