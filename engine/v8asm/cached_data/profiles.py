"""Load versioned V8 opcode and serializer profiles."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import cast


PROFILE_FORMAT_VERSION = 1
PROFILE_REQUIRED_FIELDS = frozenset({
    "version", "version_hash", "register_file_start", "has_ro_snapshot_checksum",
    "snapshot_spaces", "serializer_tags", "bytecode_array_layout",
    "shared_function_info_layout", "scope_info_layout", "runtime_default_variant",
    "runtime_variants", "runtime_variant_by_flags_hash", "intrinsic_names",
    "root_names", "root_strings", "read_only_strings", "static_root_maps",
    "static_root_area_start", "object_boilerplate_layout", "bytecodes",
})


def _header_format_for_version(version: str, has_ro_snapshot_checksum: bool) -> str:
    """Infer the historical SerializedCodeData header when older packs omit it."""
    if has_ro_snapshot_checksum:
        return "read-only-checksum"
    major_minor = tuple(int(value) for value in version.split(".")[:2])
    if major_minor >= (9, 0):
        return "legacy"
    if major_minor >= (8, 0):
        return "reservation-checksum"
    if major_minor >= (7, 4):
        return "reservation-checksum-pair"
    if major_minor >= (6, 0):
        return "legacy-code-stub-extra"
    return "legacy-code-stub"


def _version_tuple(version: str) -> tuple[int, ...]:
    if not isinstance(version, str) or not version or any(
        not part.isdigit() for part in version.split(".")
    ):
        raise ValueError(f"invalid V8 profile version: {version}")
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError as exc:
        raise ValueError(f"invalid V8 profile version: {version}") from exc


def validate_profile_data(index: dict, items: list[dict]) -> tuple[str, ...]:
    errors: list[str] = []
    if not isinstance(index, dict):
        return ("index must be an object",)
    if index.get("format") != PROFILE_FORMAT_VERSION:
        errors.append(
            f"index format must be {PROFILE_FORMAT_VERSION}, got {index.get('format')!r}"
        )
    versions = index.get("versions")
    if not isinstance(versions, list) or not all(isinstance(v, str) for v in versions):
        errors.append("index versions must be an array of strings")
        versions = []
    if len(versions) != len(set(versions)):
        errors.append("index versions contains duplicates")
    if not versions:
        errors.append("index versions must not be empty")
    for version in versions:
        try:
            _version_tuple(version)
        except ValueError as exc:
            errors.append(str(exc))
    encoding = index.get("operand_encoding")
    operand_names: set[str] = set()
    if not isinstance(encoding, dict):
        errors.append("index operand_encoding must be an object")
    else:
        for key in ("scalable_signed", "scalable_unsigned", "fixed_sizes"):
            if key not in encoding:
                errors.append(f"index operand_encoding is missing {key}")
        for key in ("scalable_signed", "scalable_unsigned"):
            values = encoding.get(key)
            if not isinstance(values, list) or not all(
                isinstance(value, str) for value in values
            ):
                errors.append(f"index operand_encoding {key} must be an array of strings")
            else:
                operand_names.update(values)
        fixed_sizes = encoding.get("fixed_sizes")
        if not isinstance(fixed_sizes, dict) or not all(
            isinstance(name, str) and isinstance(size, int) and size > 0
            for name, size in fixed_sizes.items()
        ):
            errors.append(
                "index operand_encoding fixed_sizes must map names to positive integers"
            )
        else:
            operand_names.update(fixed_sizes)

    seen_hashes: dict[int, str] = {}
    for position, item in enumerate(items):
        label = versions[position] if position < len(versions) else f"profile[{position}]"
        if not isinstance(item, dict):
            errors.append(f"{label}: profile must be an object")
            continue
        missing = sorted(PROFILE_REQUIRED_FIELDS - item.keys())
        if missing:
            errors.append(f"{label}: missing fields: {', '.join(missing)}")
        version = item.get("version")
        if version != label:
            errors.append(f"{label}: embedded version is {version!r}")
        if isinstance(version, str):
            try:
                _version_tuple(version)
            except ValueError as exc:
                errors.append(str(exc))
        version_hash = item.get("version_hash")
        if not isinstance(version_hash, int) or not 0 <= version_hash <= 0xFFFFFFFF:
            errors.append(f"{label}: version_hash must be an unsigned 32-bit integer")
        elif version_hash in seen_hashes:
            errors.append(
                f"{label}: version_hash duplicates {seen_hashes[version_hash]} "
                f"(0x{version_hash:08x})"
            )
        else:
            seen_hashes[version_hash] = label
        variants = item.get("runtime_variants")
        default_variant = item.get("runtime_default_variant")
        if not isinstance(variants, dict) or not variants:
            errors.append(f"{label}: runtime_variants must be a non-empty object")
        elif default_variant not in variants:
            errors.append(f"{label}: runtime_default_variant is not present in runtime_variants")
        bytecodes = item.get("bytecodes")
        if not isinstance(bytecodes, list) or not bytecodes:
            errors.append(f"{label}: bytecodes must be a non-empty array")
        else:
            opcode_values = [entry.get("opcode") for entry in bytecodes if isinstance(entry, dict)]
            if len(opcode_values) != len(bytecodes):
                errors.append(f"{label}: every bytecode entry must be an object")
            elif len(opcode_values) != len(set(opcode_values)):
                errors.append(f"{label}: bytecode opcode values contain duplicates")
            elif any(not isinstance(value, int) or not 0 <= value <= 255 for value in opcode_values):
                errors.append(f"{label}: opcode values must be integers from 0 through 255")
            for entry in bytecodes:
                if not isinstance(entry, dict):
                    continue
                operands = entry.get("operands")
                if not isinstance(operands, list) or not all(
                    isinstance(operand, str) for operand in operands
                ):
                    errors.append(f"{label}: bytecode operands must be an array of strings")
                    continue
                unknown = sorted(set(operands) - operand_names)
                if unknown:
                    errors.append(
                        f"{label}: unsupported operand types: {', '.join(unknown)}"
                    )
    return tuple(errors)


@dataclass(frozen=True)
class Opcode:
    value: int
    name: str
    operands: tuple[str, ...]
    jump_mode: str | None


@dataclass(frozen=True)
class BytecodeArrayLayout:
    constant_pool_slot_delta: int
    handler_table_slot_delta: int
    source_position_table_slot_delta: int
    frame_size_slot_delta: int
    parameter_encoding: str


@dataclass(frozen=True)
class SharedFunctionInfoLayout:
    function_data_slots: tuple[int, ...]
    name_or_scope_info_slots: tuple[int, ...]


@dataclass(frozen=True)
class ScopeInfoLayout:
    flags_encoding: str
    variable_part_slot: int
    module_count_before_locals: bool
    module_scope_value: int
    max_inlined_local_names: int
    scope_type_shift: int
    scope_type_mask: int
    saved_class_variable_bit: int
    function_variable_shift: int
    function_variable_mask: int
    inferred_function_name_bit: int
    flags_slot: int = 1
    context_count_slot: int = 3


@dataclass(frozen=True)
class Profile:
    version: str
    version_hash: int
    register_file_start: int
    has_ro_snapshot_checksum: bool
    snapshot_spaces: int
    serializer_tags: dict[str, int]
    bytecode_array_layout: BytecodeArrayLayout
    shared_function_info_layout: SharedFunctionInfoLayout
    scope_info_layout: ScopeInfoLayout
    runtime_default_variant: str
    runtime_variants: dict[str, tuple[str, ...]]
    runtime_variant_by_flags_hash: dict[int, str]
    intrinsic_names: tuple[str, ...]
    root_names: tuple[str, ...]
    root_strings: dict[int, str]
    read_only_strings: dict[int, str]
    read_only_strings_by_tagged_size: dict[int, dict[int, str]]
    static_root_maps: dict[int, str]
    static_root_area_start: int | None
    object_boilerplate_layout: str
    opcodes: tuple[Opcode, ...]
    source_layout: str = "unknown"
    header_format: str | None = None
    cache_header_layout: dict[str, object] | None = None

    @property
    def opcode_by_value(self) -> dict[int, Opcode]:
        return {opcode.value: opcode for opcode in self.opcodes}

    def runtime_names_for(
        self, flags_hash: int, variant: str | None = None
    ) -> tuple[str, ...]:
        selected = variant or self.runtime_variant_by_flags_hash.get(
            flags_hash, self.runtime_default_variant
        )
        try:
            return self.runtime_variants[selected]
        except KeyError as exc:
            choices = ", ".join(sorted(self.runtime_variants))
            raise ValueError(f"unknown runtime variant {selected}; choose from {choices}") from exc

    @property
    def cache_header_format(self) -> str:
        return self.header_format or _header_format_for_version(
            self.version, self.has_ro_snapshot_checksum
        )


@dataclass(frozen=True)
class ProfileSet:
    profiles: tuple[Profile, ...]
    scalable_signed: frozenset[str]
    scalable_unsigned: frozenset[str]
    fixed_sizes: dict[str, int]
    directory: Path

    def by_version(self, version: str) -> Profile:
        clean = version.removesuffix("-electron.0").split("-", 1)[0]
        for profile in self.profiles:
            if profile.version == clean:
                return profile
        requested = _version_tuple(clean)
        nearest = sorted(
            self.profiles,
            key=lambda item: sum(
                abs(left - right)
                for left, right in zip(
                    requested + (0,) * (4 - len(requested)),
                    _version_tuple(item.version) + (0,) * (4 - len(_version_tuple(item.version))),
                )
            ),
        )[:3]
        choices = ", ".join(item.version for item in nearest)
        raise ValueError(
            f"unsupported V8 version: {version}; no exact profile in {self.directory}; "
            f"nearest profiles (not used): {choices}"
        )

    def by_hash(self, version_hash: int) -> Profile:
        for profile in self.profiles:
            if profile.version_hash == version_hash:
                return profile
        raise ValueError(
            f"unknown V8 version hash 0x{version_hash:08x}; "
            f"available versions: {', '.join(profile.version for profile in self.profiles)}"
        )


class LazyProfileSet:
    """Profile catalog that reads the index first and profile JSON on demand."""

    def __init__(self, directory: Path, index: dict) -> None:
        self.directory = directory
        self.format = index["format"]
        self.versions = tuple(index["versions"])
        encoding = index["operand_encoding"]
        self.scalable_signed = frozenset(encoding["scalable_signed"])
        self.scalable_unsigned = frozenset(encoding["scalable_unsigned"])
        self.fixed_sizes = encoding["fixed_sizes"]
        self._profiles: dict[str, Profile] = {}
        self._hashes: dict[int, str] = {}

    def _load(self, version: str) -> Profile:
        clean = version.removesuffix("-electron.0").split("-", 1)[0]
        if clean not in self.versions:
            raise ValueError(f"unsupported V8 version: {version}; no exact profile in {self.directory}")
        if clean not in self._profiles:
            item = _read_json(self.directory / f"{clean}.json", f"profile {clean}")
            self._profiles[clean] = _build_profile(cast(dict, item))
            self._hashes[self._profiles[clean].version_hash] = clean
        return self._profiles[clean]

    def by_version(self, version: str) -> Profile:
        return self._load(version)

    def by_hash(self, version_hash: int) -> Profile:
        version = self._hashes.get(version_hash)
        if version is not None:
            return self._profiles[version]
        for candidate in self.versions:
            profile = self._load(candidate)
            if profile.version_hash == version_hash:
                return profile
        raise ValueError(f"unknown V8 version hash 0x{version_hash:08x}")

    @property
    def profiles(self) -> tuple[Profile, ...]:
        return tuple(self._load(version) for version in self.versions)


def _default_profile_directory() -> Path:
    return Path(__file__).with_name("profiles").resolve()


def _resolve_profile_directory(directory: str | Path | None) -> Path:
    return (
        _default_profile_directory()
        if directory is None
        else Path(directory).expanduser().resolve()
    )


def _read_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read {label} {path}: {exc}") from exc


def read_profile_data(
    directory: str | Path | None = None,
) -> tuple[dict, list[dict]]:
    profile_directory = _resolve_profile_directory(directory)
    if not profile_directory.is_dir():
        raise ValueError(f"profile directory does not exist: {profile_directory}")
    index_path = profile_directory / "index.json"
    raw = _read_json(index_path, "profile index")
    if not isinstance(raw, dict):
        raise ValueError(f"profile index must be an object: {index_path}")

    versions = raw.get("versions")
    if not isinstance(versions, list) or not all(
        isinstance(version, str) for version in versions
    ):
        errors = validate_profile_data(raw, [])
        raise ValueError("invalid cached-data profile set:\n- " + "\n- ".join(errors))
    invalid_versions = []
    for version in versions:
        try:
            _version_tuple(version)
        except ValueError as exc:
            invalid_versions.append(str(exc))
    if invalid_versions:
        raise ValueError(
            "invalid cached-data profile set:\n- "
            + "\n- ".join(invalid_versions)
        )

    profile_data = [
        _read_json(
            profile_directory / f"{version}.json",
            f"profile {version}",
        )
        for version in versions
    ]
    errors = validate_profile_data(raw, profile_data)
    if errors:
        raise ValueError("invalid cached-data profile set:\n- " + "\n- ".join(errors))
    return raw, cast(list[dict], profile_data)


def read_profile_index(directory: str | Path | None = None) -> dict:
    """Read only the compact profile index without loading profile JSON files."""
    profile_directory = _resolve_profile_directory(directory)
    if not profile_directory.is_dir():
        raise ValueError(f"profile directory does not exist: {profile_directory}")
    raw = _read_json(profile_directory / "index.json", "profile index")
    if not isinstance(raw, dict) or raw.get("format") != PROFILE_FORMAT_VERSION:
        raise ValueError(f"invalid cached-data profile index: {profile_directory / 'index.json'}")
    versions = raw.get("versions")
    if not isinstance(versions, list) or not all(isinstance(version, str) for version in versions):
        raise ValueError("profile index versions must be an array of strings")
    return {**raw, "directory": profile_directory}


def _build_profile(item: dict) -> Profile:
    return Profile(
            version=item["version"],
            version_hash=item["version_hash"],
            register_file_start=item["register_file_start"],
            has_ro_snapshot_checksum=item["has_ro_snapshot_checksum"],
            snapshot_spaces=item["snapshot_spaces"],
            serializer_tags=item["serializer_tags"],
            bytecode_array_layout=BytecodeArrayLayout(**item["bytecode_array_layout"]),
            shared_function_info_layout=SharedFunctionInfoLayout(
                function_data_slots=tuple(
                    item["shared_function_info_layout"]["function_data_slots"]
                ),
                name_or_scope_info_slots=tuple(
                    item["shared_function_info_layout"]["name_or_scope_info_slots"]
                ),
            ),
            scope_info_layout=ScopeInfoLayout(**item["scope_info_layout"]),
            runtime_default_variant=item["runtime_default_variant"],
            runtime_variants={
                name: tuple(names) for name, names in item["runtime_variants"].items()
            },
            runtime_variant_by_flags_hash={
                int(value, 0): name
                for value, name in item["runtime_variant_by_flags_hash"].items()
            },
            intrinsic_names=tuple(item["intrinsic_names"]),
            root_names=tuple(item["root_names"]),
            root_strings={int(index): value for index, value in item["root_strings"].items()},
            read_only_strings={
                int(offset): value for offset, value in item["read_only_strings"].items()
            },
            read_only_strings_by_tagged_size={
                int(tagged_size): {
                    int(offset): value for offset, value in values.items()
                }
                for tagged_size, values in item.get(
                    "read_only_strings_by_tagged_size", {}
                ).items()
            },
            static_root_maps={
                int(pointer): name
                for pointer, name in item["static_root_maps"].items()
            },
            static_root_area_start=item["static_root_area_start"],
            object_boilerplate_layout=item.get(
                "object_boilerplate_layout", "fixed_array"
            ),
            opcodes=tuple(
                Opcode(
                    entry["opcode"],
                    entry["name"],
                    tuple(entry["operands"]),
                    entry["jump_mode"],
                )
                for entry in item["bytecodes"]
            ),
            source_layout=item.get("source_layout", "unknown"),
            header_format=item.get("header_format"),
            cache_header_layout=item.get("cache_header_layout"),
        )


@lru_cache(maxsize=8)
def _load_profiles_cached(directory: str) -> ProfileSet:
    profile_directory = Path(directory)
    raw, profile_data = read_profile_data(profile_directory)
    profiles = tuple(_build_profile(item) for item in profile_data)
    encoding = raw["operand_encoding"]
    return ProfileSet(
        profiles=profiles,
        scalable_signed=frozenset(encoding["scalable_signed"]),
        scalable_unsigned=frozenset(encoding["scalable_unsigned"]),
        fixed_sizes=encoding["fixed_sizes"],
        directory=profile_directory,
    )


def load_profiles(directory: str | Path | None = None) -> ProfileSet:
    return _load_profiles_cached(str(_resolve_profile_directory(directory)))


@lru_cache(maxsize=8)
def _load_profiles_lazy_cached(directory: str) -> LazyProfileSet:
    profile_directory = Path(directory)
    index = read_profile_index(profile_directory)
    return LazyProfileSet(profile_directory, index)


def load_profiles_lazy(directory: str | Path | None = None) -> LazyProfileSet:
    return _load_profiles_lazy_cached(str(_resolve_profile_directory(directory)))
