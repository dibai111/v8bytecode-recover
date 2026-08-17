import unittest

try:
    from .legacy_snapshot import (
        SNAPSHOT_DATA_MAGIC,
        LegacySnapshotData,
        locate_legacy_snapshot,
        parse_legacy_snapshot,
    )
    from .profiles import load_profiles
except ImportError:  # unittest discover with cached_data as the top-level path.
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from cached_data.legacy_snapshot import (  # type: ignore[no-redef]
        SNAPSHOT_DATA_MAGIC,
        LegacySnapshotData,
        locate_legacy_snapshot,
        parse_legacy_snapshot,
    )
    from cached_data.profiles import load_profiles  # type: ignore[no-redef]


def encode_uint30(value: int) -> bytes:
    encoded = value << 2
    size = 1
    while encoded >= 1 << (size * 8):
        size += 1
    if size > 4:
        raise ValueError("test value does not fit uint30")
    return (encoded | (size - 1)).to_bytes(size, "little")


def synthetic_snapshot(profile) -> LegacySnapshotData:
    map_index = profile.root_names.index("one_byte_string_map")
    value = b"hello"
    raw = b"\0\0\0\0" + len(value).to_bytes(4, "little") + value
    raw = raw.ljust(16, b"\0")
    payload = b"".join(
        (
            bytes([0]),
            encode_uint30(3),
            bytes([profile.serializer_tags["RootArrayConstants"] + map_index]),
            bytes([profile.serializer_tags["FixedRawData"] + 1]),
            raw,
            bytes([profile.serializer_tags["Synchronize"]]),
            bytes([profile.serializer_tags["RootArrayConstants"] + 4]),
            bytes([profile.serializer_tags["Synchronize"]]),
        )
    )
    data = SNAPSHOT_DATA_MAGIC.to_bytes(4, "little") + len(payload).to_bytes(
        4, "little"
    ) + payload
    return LegacySnapshotData(data, profile.version, None)


class LegacySnapshotTests(unittest.TestCase):
    def test_decodes_a_minimal_legacy_string_heap(self):
        profile = load_profiles().by_version("10.2.154.4")
        snapshot = synthetic_snapshot(profile)

        image = parse_legacy_snapshot(snapshot, profile, 8, ((0, 0x2130),))

        self.assertEqual(image.root_count, 1)
        self.assertEqual(image.cache_count, 0)
        self.assertEqual(image.object_count, 1)
        self.assertEqual(image.area_start_offset, 0x2130)
        self.assertEqual(image.string_at(0, 0x2130), "hello")

    def test_locates_raw_snapshot_data(self):
        profile = load_profiles().by_version("10.2.154.4")
        snapshot = synthetic_snapshot(profile)

        located = locate_legacy_snapshot(snapshot.data, profile)

        self.assertEqual(located.data, snapshot.data)
        self.assertEqual(located.version, profile.version)


if __name__ == "__main__":
    unittest.main()
