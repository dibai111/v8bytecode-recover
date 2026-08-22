"""Parse and validate V8 cached-data headers across historical layouts."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .profiles import Profile, ProfileSet


@dataclass(frozen=True)
class CacheHeader:
    magic: int
    version_hash: int
    source_hash: int
    flags_hash: int
    ro_snapshot_checksum: int | None
    payload_length: int
    checksum: int
    header_size: int
    raw_payload: bool = False
    format: str = "legacy"
    cpu_features: int | None = None
    reservation_count: int = 0
    code_stub_key_count: int = 0
    reservations: tuple[int, ...] = ()
    external_reference_count: int | None = None
    checksum_part_b: int | None = None
    base_header_size: int = 0


@dataclass(frozen=True)
class _HeaderLayout:
    name: str
    base_size: int
    version_offset: int
    source_offset: int
    flags_offset: int
    payload_offset: int
    checksum_offsets: tuple[int, ...]
    cpu_features_offset: int | None = None
    ro_snapshot_offset: int | None = None
    reservation_count_offset: int | None = None
    code_stub_key_count_offset: int | None = None
    external_reference_count_offset: int | None = None


_LAYOUTS = {
    "legacy": _HeaderLayout(
        "legacy", 24, 4, 8, 12, 16, (20,)
    ),
    "read-only-checksum": _HeaderLayout(
        "read-only-checksum", 28, 4, 8, 12, 20, (24,),
        ro_snapshot_offset=16,
    ),
    "reservation-checksum": _HeaderLayout(
        "reservation-checksum", 28, 4, 8, 12, 20, (24,),
        reservation_count_offset=16,
    ),
    "reservation-checksum-pair": _HeaderLayout(
        "reservation-checksum-pair", 32, 4, 8, 12, 20, (24, 28),
        reservation_count_offset=16,
    ),
    "legacy-code-stub": _HeaderLayout(
        "legacy-code-stub", 40, 4, 8, 16, 28, (32, 36),
        cpu_features_offset=12,
        reservation_count_offset=24,
        code_stub_key_count_offset=20,
    ),
    "legacy-code-stub-extra": _HeaderLayout(
        "legacy-code-stub-extra", 44, 8, 12, 20, 32, (36, 40),
        cpu_features_offset=16,
        reservation_count_offset=28,
        code_stub_key_count_offset=24,
        external_reference_count_offset=4,
    ),
}


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) & -alignment


def _uint32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _profile_layout(profile: Profile) -> _HeaderLayout:
    """Use source-derived offsets when a profile contains them."""
    raw = profile.cache_header_layout
    if not isinstance(raw, dict):
        try:
            return _LAYOUTS[profile.cache_header_format]
        except KeyError as exc:
            raise ValueError(
                f"profile {profile.version} declares unsupported cached-data header format: "
                f"{profile.cache_header_format}"
            ) from exc

    try:
        checksum_offsets = tuple(int(value) for value in raw["checksum_offsets"])
        return _HeaderLayout(
            name=str(raw.get("name", profile.cache_header_format)),
            base_size=int(raw["base_size"]),
            version_offset=int(raw["version_offset"]),
            source_offset=int(raw["source_offset"]),
            flags_offset=int(raw["flags_offset"]),
            payload_offset=int(raw["payload_offset"]),
            checksum_offsets=checksum_offsets,
            cpu_features_offset=(
                int(raw["cpu_features_offset"])
                if raw.get("cpu_features_offset") is not None
                else None
            ),
            ro_snapshot_offset=(
                int(raw["ro_snapshot_offset"])
                if raw.get("ro_snapshot_offset") is not None
                else None
            ),
            reservation_count_offset=(
                int(raw["reservation_count_offset"])
                if raw.get("reservation_count_offset") is not None
                else None
            ),
            code_stub_key_count_offset=(
                int(raw["code_stub_key_count_offset"])
                if raw.get("code_stub_key_count_offset") is not None
                else None
            ),
            external_reference_count_offset=(
                int(raw["external_reference_count_offset"])
                if raw.get("external_reference_count_offset") is not None
                else None
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"profile {profile.version} contains an invalid cache_header_layout"
        ) from exc


def _parse_layout(data: bytes, layout: _HeaderLayout) -> dict[str, object]:
    if len(data) < layout.base_size:
        raise ValueError(
            f"{layout.name} header requires at least {layout.base_size} bytes"
        )

    reservation_count = (
        _uint32(data, layout.reservation_count_offset)
        if layout.reservation_count_offset is not None
        else 0
    )
    stub_count = (
        _uint32(data, layout.code_stub_key_count_offset)
        if layout.code_stub_key_count_offset is not None
        else 0
    )
    metadata_size = (reservation_count + stub_count) * 4
    metadata_end = layout.base_size + metadata_size
    if metadata_end > len(data) or reservation_count > 1_000_000 or stub_count > 1_000_000:
        raise ValueError(f"{layout.name} header metadata counts exceed file bounds")

    payload_length = _uint32(data, layout.payload_offset)
    payload_start = len(data) - payload_length
    possible_starts = {
        _align(metadata_end, 4),
        _align(metadata_end, 8),
    }
    if payload_start not in possible_starts:
        expected = ", ".join(str(value) for value in sorted(possible_starts))
        raise ValueError(
            f"{layout.name} payload starts at {payload_start}, expected {expected}"
        )
    if any(data[metadata_end:payload_start]):
        raise ValueError(f"{layout.name} header padding is not zero")

    reservations = tuple(
        _uint32(data, layout.base_size + index * 4)
        for index in range(reservation_count)
    )
    checksums = tuple(_uint32(data, offset) for offset in layout.checksum_offsets)
    return {
        "magic": _uint32(data, 0),
        "version_hash": _uint32(data, layout.version_offset),
        "source_hash": _uint32(data, layout.source_offset),
        "flags_hash": _uint32(data, layout.flags_offset),
        "ro_snapshot_checksum": (
            _uint32(data, layout.ro_snapshot_offset)
            if layout.ro_snapshot_offset is not None
            else None
        ),
        "payload_length": payload_length,
        "checksum": checksums[0],
        "checksum_part_b": checksums[1] if len(checksums) > 1 else None,
        "header_size": payload_start,
        "format": layout.name,
        "cpu_features": (
            _uint32(data, layout.cpu_features_offset)
            if layout.cpu_features_offset is not None
            else None
        ),
        "reservation_count": reservation_count,
        "code_stub_key_count": stub_count,
        "reservations": reservations,
        "external_reference_count": (
            _uint32(data, layout.external_reference_count_offset)
            if layout.external_reference_count_offset is not None
            else None
        ),
        "base_header_size": layout.base_size,
    }


def _profile_for_header(data: bytes, profiles: ProfileSet) -> Profile:
    candidates: list[Profile] = []
    for profile in profiles.profiles:
        try:
            layout = _profile_layout(profile)
        except ValueError:
            continue
        if len(data) < layout.version_offset + 4:
            continue
        if _uint32(data, layout.version_offset) == profile.version_hash:
            candidates.append(profile)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        versions = ", ".join(profile.version for profile in candidates)
        raise ValueError(f"cached-data version hash matches multiple profiles: {versions}")
    observed = []
    limit = min(len(data) - 4, 256)
    known_hashes = {profile.version_hash: profile.version for profile in profiles.profiles}
    for offset in range(0, max(limit, -1) + 1, 4):
        value = _uint32(data, offset)
        version = known_hashes.get(value)
        if version is not None:
            observed.append(f"offset {offset}=0x{value:08x} ({version})")
    hints = ", ".join(observed) or (
        f"offsets 4/8=0x{_uint32(data, 4):08x}/0x{_uint32(data, 8):08x}"
    )
    raise ValueError(
        f"unknown V8 version hash: candidates are {hints}; "
        f"profiles in {profiles.directory}: "
        + ", ".join(profile.version for profile in profiles.profiles)
    )


def parse_header(
    data: bytes, profiles: ProfileSet, version: str | None = None
) -> tuple[CacheHeader, Profile]:
    if len(data) < 24:
        raise ValueError("file is shorter than the minimum V8 code-cache header")

    profile = profiles.by_version(version) if version else _profile_for_header(data, profiles)
    layout = _profile_layout(profile)
    format_name = layout.name

    try:
        values = _parse_layout(data, layout)
    except ValueError as exc:
        raise ValueError(
            f"cached-data header does not match V8 {profile.version} "
            f"({format_name}): {exc}"
        ) from exc

    if values["version_hash"] != profile.version_hash:
        raise ValueError(
            f"cached-data version hash 0x{values['version_hash']:08x} does not "
            f"match profile {profile.version} (0x{profile.version_hash:08x})"
        )
    return CacheHeader(**values), profile
