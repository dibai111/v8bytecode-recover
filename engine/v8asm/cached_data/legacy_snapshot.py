"""Read legacy V8 read-only snapshots and embedded Node snapshots."""

from __future__ import annotations

from dataclasses import dataclass
import zlib

from .object_stream import (
    ObjectStreamParser,
    ParseError,
    RawChunk,
    Reference,
    SerializedObject,
)
from .profiles import Profile


SNAPSHOT_DATA_MAGIC = 0xC0DE0562
LEGACY_AREA_START_OFFSET = 0x1130
MAX_SNAPSHOT_PAYLOAD = 512 * 1024 * 1024


@dataclass(frozen=True)
class LegacySnapshotData:
    data: bytes
    version: str
    checksum: int | None


@dataclass(frozen=True)
class LegacySnapshotImage:
    version: str
    magic: int
    checksum: int
    area_start_offset: int
    root_locations: dict[tuple[int, int], str]
    strings: dict[tuple[int, int], str]
    root_count: int
    cache_count: int
    object_count: int

    def string_at(self, page_index: int, chunk_offset: int) -> str | None:
        """Return a decoded sequential string at a legacy heap address."""
        return self.strings.get((page_index, chunk_offset))


def _uint32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise ValueError("snapshot header is truncated")
    return int.from_bytes(data[offset : offset + 4], "little")


def _snapshot_data_length(data: bytes) -> int:
    if len(data) < 8:
        raise ValueError("snapshot data is shorter than its header")
    magic = _uint32(data, 0)
    if magic != SNAPSHOT_DATA_MAGIC:
        raise ValueError(f"unexpected snapshot data magic 0x{magic:08x}")
    payload_length = _uint32(data, 4)
    if payload_length > MAX_SNAPSHOT_PAYLOAD or 8 + payload_length > len(data):
        raise ValueError("snapshot data payload exceeds its buffer")
    return payload_length


def _version_from_header(data: bytes, profile: Profile) -> str:
    version = data[12:76].split(b"\0", 1)[0].decode("ascii", errors="strict")
    if version.split("-", 1)[0] != profile.version:
        raise ValueError(
            f"snapshot V8 version {version or '<empty>'} does not match "
            f"profile {profile.version}"
        )
    return version


def _decode_embedded_section(section: bytes) -> bytes | None:
    if section[:4] == SNAPSHOT_DATA_MAGIC.to_bytes(4, "little"):
        _snapshot_data_length(section)
        return section
    if len(section) < 5:
        return None
    uncompressed_size = _uint32(section, 0)
    if not 8 <= uncompressed_size <= MAX_SNAPSHOT_PAYLOAD:
        return None
    try:
        decoded = zlib.decompress(section[4:], -15)
    except zlib.error:
        return None
    if len(decoded) != uncompressed_size:
        return None
    if decoded[:4] != SNAPSHOT_DATA_MAGIC.to_bytes(4, "little"):
        return None
    _snapshot_data_length(decoded)
    return decoded


@dataclass(frozen=True)
class EmbeddedReadOnlyImage:
    """Read-only heap section extracted from an embedder binary (V8 10.10+)."""

    version: str
    checksum: int
    section: bytes

    @staticmethod
    def _external_blob(version: str, checksum: int, section: bytes, magic: int) -> bytes:
        """Wrap an embedded read-only section in the external snapshot-blob
        layout that ReadOnlySnapshot.parse expects.

        The wrapped checksum is replaced by the caller afterwards because the
        embedded header stores the startup-blob checksum, not the read-only
        checksum that cached-data headers compare against.
        """
        import struct

        header = bytearray(88)
        struct.pack_into("<I", header, 12, checksum)
        version_bytes = version.encode("ascii")
        header[16 : 16 + len(version_bytes)] = version_bytes
        struct.pack_into("<I", header, 80, 88)
        struct.pack_into("<I", header, 84, 88 + 4 + len(section))
        return bytes(header) + struct.pack("<I", magic) + section

    def external_blob(self, magic: int, ro_checksum: int | None = None) -> bytes:
        return self._external_blob(
            self.version,
            self.checksum if ro_checksum is None else ro_checksum,
            self.section,
            magic,
        )


def embedded_read_only_heap_sections(
    data: bytes, profile: Profile
) -> tuple[EmbeddedReadOnlyImage, ...]:
    """Extract read-only heap sections from embedders whose V8 (10.10+) stores
    the serialized image without SnapshotData magic, e.g. official Node builds.

    The embedded startup blob places a u32 context count followed by the V8
    version string; offsets at +76/+80 locate the read-only section, which
    begins with a u32 uncompressed size and the raw page payload.
    """
    marker = profile.version.encode("ascii")
    candidates: list[EmbeddedReadOnlyImage] = []
    seen: set[bytes] = set()
    search_from = 0
    while True:
        at = data.find(marker, search_from)
        if at < 0:
            break
        search_from = at + 1
        base = at - 12
        if base < 0 or base + 84 > len(data):
            continue
        contexts = _uint32(data, base)
        if not 1 <= contexts <= 64:
            continue
        try:
            version = _version_from_header(data[base:], profile)
        except (UnicodeDecodeError, ValueError):
            continue
        if not version.startswith(profile.version):
            continue
        read_only_offset = _uint32(data, base + 76)
        shared_heap_offset = _uint32(data, base + 80)
        if not 84 <= read_only_offset < shared_heap_offset <= len(data) - base:
            continue
        section = data[base + read_only_offset : base + shared_heap_offset]
        if len(section) < 8:
            continue
        declared_size = _uint32(section, 0)
        payload = section[8:]
        if declared_size != len(payload):
            continue
        if section in seen:
            continue
        seen.add(section)
        candidates.append(
            EmbeddedReadOnlyImage(
                version=version,
                checksum=_uint32(data, base + 4),
                section=section,
            )
        )
    return tuple(candidates)


def embedded_read_only_snapshots(data: bytes, profile: Profile) -> tuple[LegacySnapshotData, ...]:
    """Extract compressed read-only SnapshotData sections from a Node binary."""
    marker = profile.version.encode("ascii")
    candidates: list[LegacySnapshotData] = []
    seen: set[bytes] = set()
    search_from = 0
    while True:
        version_offset = data.find(marker, search_from)
        if version_offset < 0:
            break
        search_from = version_offset + len(marker)
        base = version_offset - 12
        if base < 0 or base + 84 > len(data):
            continue
        try:
            version = _version_from_header(data[base : base + 84], profile)
            contexts = _uint32(data, base)
            read_only_offset = _uint32(data, base + 76)
            shared_heap_offset = _uint32(data, base + 80)
        except (UnicodeDecodeError, ValueError):
            continue
        if not 1 <= contexts <= 64:
            continue
        if not 84 + contexts * 4 <= read_only_offset < shared_heap_offset:
            continue
        section_start = base + read_only_offset
        section_end = base + shared_heap_offset
        if section_end > len(data):
            continue
        snapshot_data = _decode_embedded_section(data[section_start:section_end])
        if snapshot_data is None or snapshot_data in seen:
            continue
        seen.add(snapshot_data)
        candidates.append(
            LegacySnapshotData(
                data=snapshot_data,
                version=version,
                checksum=_uint32(data, base + 8),
            )
        )
    return tuple(candidates)


def locate_legacy_snapshot(data: bytes, profile: Profile) -> LegacySnapshotData:
    """Accept raw SnapshotData, a legacy startup blob, or an embedded Node binary."""
    if data[:4] == SNAPSHOT_DATA_MAGIC.to_bytes(4, "little"):
        _snapshot_data_length(data)
        return LegacySnapshotData(data=data, version=profile.version, checksum=None)

    if len(data) >= 84:
        try:
            version = _version_from_header(data, profile)
            read_only_offset = _uint32(data, 76)
            shared_heap_offset = _uint32(data, 80)
            if 84 <= read_only_offset < shared_heap_offset <= len(data):
                section = data[read_only_offset:shared_heap_offset]
                snapshot_data = _decode_embedded_section(section)
                if snapshot_data is not None:
                    return LegacySnapshotData(
                        data=snapshot_data,
                        version=version,
                        checksum=_uint32(data, 8),
                    )
        except (UnicodeDecodeError, ValueError):
            pass

    candidates = embedded_read_only_snapshots(data, profile)
    if candidates:
        return candidates[0]
    raise ValueError(
        "no compatible legacy read-only SnapshotData was found in the supplied file"
    )


class _LegacyObjectStreamParser(ObjectStreamParser):
    """Parse the object stream while reproducing read-only allocation order."""

    def __init__(self, payload: bytes, profile: Profile, tagged_size: int) -> None:
        super().__init__(payload, profile, tagged_size)
        self.space_cursors = [0] * profile.snapshot_spaces
        self.locations: dict[tuple[int, int], int] = {}
        self._code_body_count = 0

    def _allocate(self, obj: SerializedObject) -> None:
        cursor = self.space_cursors[obj.space]
        alignment = self.tagged_size
        offset = (cursor + alignment - 1) & ~(alignment - 1)
        obj.allocation_offset = offset
        self.space_cursors[obj.space] = offset + obj.size
        self.locations[(obj.space, offset)] = obj.index

    def _object(self, space: int) -> SerializedObject:
        start = self.reader.position - 1
        size_in_tagged = self.reader.uint30()
        size = size_in_tagged * self.tagged_size
        if size < self.tagged_size or size > MAX_SNAPSHOT_PAYLOAD:
            raise ParseError(f"implausible serialized object size {size}")
        _, map_reference = self._reference(self.reader.byte(), None, 0)
        obj = SerializedObject(len(self.objects), space, size, start)
        self.objects.append(obj)
        obj.map_reference = map_reference
        self._allocate(obj)

        current = 1
        while current < size_in_tagged:
            tag = self.reader.byte()
            consumed, reference = self._reference(tag, obj, current)
            if consumed < 0 or current + consumed > size_in_tagged:
                raise ParseError(f"serializer entry overruns object {obj.index}")
            if reference is not None and consumed:
                for repeat_slot in range(consumed):
                    obj.references[(current + repeat_slot) * self.tagged_size] = reference
            current += consumed
        if current != size_in_tagged:
            raise ParseError(
                f"object {obj.index} ended at slot {current}, "
                f"expected {size_in_tagged}"
            )
        return obj

    def _meta_object(self) -> SerializedObject:
        start = self.reader.position - 1
        size = 40 if self.tagged_size == 4 else 72
        size_in_tagged = size // self.tagged_size
        obj = SerializedObject(len(self.objects), 0, size, start)
        self.objects.append(obj)
        obj.map_reference = Reference("object", object_index=obj.index)
        self._allocate(obj)

        current = 1
        while current < size_in_tagged:
            tag = self.reader.byte()
            consumed, reference = self._reference(tag, obj, current)
            if consumed < 0 or current + consumed > size_in_tagged:
                raise ParseError(f"serializer entry overruns meta-map {obj.index}")
            if reference is not None and consumed:
                for repeat_slot in range(consumed):
                    obj.references[(current + repeat_slot) * self.tagged_size] = reference
            current += consumed
        if current != size_in_tagged:
            raise ParseError(
                f"meta-map {obj.index} ended at slot {current}, "
                f"expected {size_in_tagged}"
            )
        return obj

    def _reference(
        self, tag: int, obj: SerializedObject | None, slot: int
    ) -> tuple[int, Reference | None]:
        if tag != self.tags.get("CodeBody"):
            return super()._reference(tag, obj, slot)

        body_size_in_tagged = self.reader.uint30()
        raw_start, raw = self.reader.raw(body_size_in_tagged * self.tagged_size)
        code_data_start = 40 if self.tagged_size == 8 else 24
        if obj is not None:
            obj.raw_chunks.append(RawChunk(code_data_start, raw_start, raw))

        header_slots = code_data_start // self.tagged_size - 1
        for header_slot in range(1, header_slots + 1):
            header_tag = self.reader.byte()
            consumed, reference = super()._reference(header_tag, obj, header_slot)
            if consumed != 1:
                raise ParseError("CodeBody header reference consumed an invalid slot count")
            if obj is not None and reference is not None:
                obj.references[header_slot * self.tagged_size] = reference

        # CodeBody stores the objects needed by relocations after its header.
        # RelocInfo itself is reconstructed from the raw code bytes; there is
        # no second relocation byte stream in SnapshotData.
        synchronize = self.tags["Synchronize"]
        while True:
            tag = self.reader.byte()
            if tag == synchronize:
                break
            consumed, _ = super()._reference(tag, None, 0)
            if consumed != 1:
                raise ParseError(
                    "CodeBody pre-serialized object did not consume one slot"
                )

        self._code_body_count += 1
        return header_slots + body_size_in_tagged, None

    def parse_legacy(self) -> tuple[list[Reference | None], int]:
        roots: list[Reference | None] = []
        synchronize = self.tags["Synchronize"]
        while True:
            tag = self.reader.byte()
            if tag == synchronize:
                break
            consumed, reference = self._reference(tag, None, len(roots))
            if consumed != 1:
                raise ParseError("legacy snapshot root entry did not consume one slot")
            roots.append(reference)

        cache_count = 0
        terminal = self.tags["RootArrayConstants"] + 4
        while True:
            tag = self.reader.byte()
            consumed, _ = self._reference(tag, None, 0)
            if consumed != 1:
                raise ParseError("legacy snapshot cache entry did not consume one slot")
            if tag == terminal:
                break
            cache_count += 1

        if self.reader.byte() != synchronize:
            raise ParseError("legacy snapshot deferred-object terminator is missing")
        while self.reader.position < len(self.reader.data):
            if self.reader.byte() != self.tags["Nop"]:
                raise ParseError("non-padding data follows the legacy snapshot terminator")
        if self.pending_forward_ref_count:
            raise ParseError("legacy snapshot has unresolved forward references")
        return roots, cache_count

    def _root_object_index(
        self,
        reference: Reference | None,
        roots: list[Reference | None],
        visiting: set[int],
    ) -> int | None:
        if reference is None:
            return None
        if reference.object_index is not None:
            return reference.object_index
        if reference.kind != "root" or not reference.values:
            return None
        root_index = reference.values[0]
        if root_index in visiting or not 0 <= root_index < len(roots):
            return None
        visiting.add(root_index)
        result = self._root_object_index(roots[root_index], roots, visiting)
        visiting.remove(root_index)
        return result

    def _root_name(self, reference: Reference | None) -> str | None:
        if reference is None or reference.kind != "root" or not reference.values:
            return None
        index = reference.values[0]
        return self.profile.root_names[index] if index < len(self.profile.root_names) else None

    def _map_name(self, obj: SerializedObject, visiting: set[int] | None = None) -> str | None:
        reference = obj.map_reference
        if reference is None:
            return None
        direct = self._root_name(reference)
        if direct is not None:
            return direct
        if reference.object_index is None:
            return None
        visiting = visiting or set()
        if obj.index in visiting or not 0 <= reference.object_index < len(self.objects):
            return None
        visiting.add(obj.index)
        result = self._map_name(self.objects[reference.object_index], visiting)
        visiting.remove(obj.index)
        return result

    def _decode_string(self, obj: SerializedObject) -> str | None:
        map_name = self._map_name(obj)
        if map_name is None:
            return None
        normalized = map_name.replace("_", "").lower()
        if "stringmap" not in normalized:
            return None
        if "onebyte" in normalized:
            width, encoding = 1, "latin-1"
        elif "twobyte" in normalized:
            width, encoding = 2, "utf-16-le"
        else:
            return None
        if any(kind in normalized for kind in ("cons", "external", "sliced", "thin")):
            return None

        image, present = obj.image()
        length_offset = self.tagged_size + 4
        data_start = self.tagged_size + 8
        if length_offset + 4 > obj.size or not all(
            present[length_offset : length_offset + 4]
        ):
            return None
        length = int.from_bytes(image[length_offset : length_offset + 4], "little")
        if length > 16 * 1024 * 1024:
            return None
        data_end = data_start + length * width
        aligned_size = (data_end + self.tagged_size - 1) & ~(self.tagged_size - 1)
        if data_end > obj.size or aligned_size != obj.size:
            return None
        if not all(present[data_start:data_end]):
            return None
        try:
            return image[data_start:data_end].decode(encoding)
        except UnicodeDecodeError:
            return None

    def _area_candidates(
        self, hints: tuple[tuple[int, int], ...]
    ) -> set[int]:
        read_only_locations = {
            offset
            for (space, offset), _ in self.locations.items()
            if space == 0
        }
        if not read_only_locations:
            raise ValueError("legacy snapshot does not allocate read-only objects")
        candidates: set[int] | None = None
        for page, reference_offset in hints:
            if page != 0:
                raise ValueError(
                    "legacy read-only references use more than one page, "
                    "which this snapshot layout cannot map"
                )
            current = {
                reference_offset - object_offset
                for object_offset in read_only_locations
                if reference_offset >= object_offset
            }
            candidates = current if candidates is None else candidates & current
        return candidates or {LEGACY_AREA_START_OFFSET}

    def build_image(
        self,
        roots: list[Reference | None],
        cache_count: int,
        hints: tuple[tuple[int, int], ...],
        version: str,
        magic: int,
        checksum: int,
    ) -> LegacySnapshotImage:
        candidates = self._area_candidates(hints)
        scored: list[tuple[int, int, dict[tuple[int, int], str]]] = []
        for area_start in candidates:
            values: dict[tuple[int, int], str] = {}
            for (space, offset), object_index in self.locations.items():
                if space != 0:
                    continue
                value = self._decode_string(self.objects[object_index])
                if value is not None:
                    values[(0, area_start + offset)] = value
            score = sum(reference in values for reference in hints)
            scored.append((score, area_start, values))

        best_score = max(score for score, _, _ in scored)
        best = [item for item in scored if item[0] == best_score]
        if hints and best_score != len(hints):
            raise ValueError(
                "read-only references cannot be mapped to the legacy snapshot "
                f"(matched {best_score} of {len(hints)} offsets)"
            )
        if len(best) != 1:
            preferred = [
                item for item in best if item[1] == LEGACY_AREA_START_OFFSET
            ]
            if len(preferred) != 1:
                raise ValueError("legacy snapshot read-only allocation area is ambiguous")
            best = preferred
        _, area_start, strings = best[0]

        root_locations: dict[tuple[int, int], str] = {}
        for index, reference in enumerate(roots):
            object_index = self._root_object_index(reference, roots, set())
            if object_index is None or not 0 <= object_index < len(self.objects):
                continue
            obj = self.objects[object_index]
            if obj.space != 0 or obj.allocation_offset is None:
                continue
            name = self.profile.root_names[index] if index < len(self.profile.root_names) else ""
            root_locations[(0, area_start + obj.allocation_offset)] = name

        return LegacySnapshotImage(
            version=version,
            magic=magic,
            checksum=checksum,
            area_start_offset=area_start,
            root_locations=root_locations,
            strings=strings,
            root_count=len(roots),
            cache_count=cache_count,
            object_count=len(self.objects),
        )


def parse_legacy_snapshot(
    data: LegacySnapshotData,
    profile: Profile,
    tagged_size: int,
    hints: tuple[tuple[int, int], ...] = (),
) -> LegacySnapshotImage:
    payload_length = _snapshot_data_length(data.data)
    payload = data.data[8 : 8 + payload_length]
    parser = _LegacyObjectStreamParser(payload, profile, tagged_size)
    roots, cache_count = parser.parse_legacy()
    return parser.build_image(
        roots,
        cache_count,
        hints,
        data.version,
        _uint32(data.data, 0),
        data.checksum or 0,
    )


__all__ = [
    "LEGACY_AREA_START_OFFSET",
    "EmbeddedReadOnlyImage",
    "LegacySnapshotData",
    "LegacySnapshotImage",
    "SNAPSHOT_DATA_MAGIC",
    "embedded_read_only_heap_sections",
    "embedded_read_only_snapshots",
    "locate_legacy_snapshot",
    "parse_legacy_snapshot",
]
