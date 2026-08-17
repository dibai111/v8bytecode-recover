#!/usr/bin/env python3
"""Developer tool for generating cached-data profiles from V8 sources."""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path

VERSIONS = (
    "9.4.146.24",
    "10.2.154.4",
    "10.2.154.26",
    "10.8.168.25",
    "11.3.244.8",
    "11.4.183.14",
    "11.9.169.7",
    "12.4.254.12",
    "12.4.254.21",
    "12.9.202.28",
    "13.2.152.41",
    "13.4.114.14",
    "13.4.114.21",
    "13.6.233.8",
    "13.6.233.10",
    "14.7.57",
    "15.2.7953087",
)

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "profiles"

SCALABLE_SIGNED = {
    "Imm",
    "Reg",
    "RegList",
    "RegPair",
    "RegOut",
    "RegOutList",
    "RegOutPair",
    "RegOutTriple",
    "RegInOut",
}
SCALABLE_UNSIGNED = {"Idx", "UImm", "RegCount"}
FIXED_SIZES = {
    "Flag8": 1,
    "Flag16": 2,
    "IntrinsicId": 1,
    "RuntimeId": 2,
    "NativeContextIndex": 1,
}

RUNTIME_VARIANT_BY_FLAGS_HASH = {
    "13.2.152.41": {
        "0x43f91081": "leaptiering",
        "0x5d3755f3": "leaptiering",
        "0xcc09158f": "legacy",
    }
}


class SourceProvider(ABC):
    """Read an exact file from an exact V8 tag."""

    @abstractmethod
    def read(self, version: str, path: str) -> str:
        raise NotImplementedError

    def read_optional(self, version: str, path: str) -> str | None:
        try:
            return self.read(version, path)
        except (FileNotFoundError, urllib.error.HTTPError):
            return None


class GitRepositoryProvider(SourceProvider):
    def __init__(self, repository: Path):
        self.repository = repository

    def read(self, version: str, path: str) -> str:
        try:
            result = subprocess.run(
                ["git", "show", f"{version}:{path}"],
                cwd=self.repository,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("git executable was not found") from exc
        return result.stdout

    def read_optional(self, version: str, path: str) -> str | None:
        try:
            return self.read(version, path)
        except (RuntimeError, subprocess.CalledProcessError):
            return None


class GitHubRawProvider(SourceProvider):
    def __init__(self, repository: str, cache_dir: Path):
        self.repository = repository.strip("/")
        self.cache_dir = cache_dir

    def read(self, version: str, path: str) -> str:
        cache_path = self.cache_dir / version / Path(path)
        if cache_path.is_file():
            return cache_path.read_text(encoding="utf-8")
        url = f"https://raw.githubusercontent.com/{self.repository}/{version}/{path}"
        request = urllib.request.Request(
            url, headers={"User-Agent": "v8bytecode-recover-profile-generator"}
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    source = response.read().decode("utf-8")
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 404 or attempt == 2:
                    raise
                time.sleep(0.5 * (attempt + 1))
            except (TimeoutError, urllib.error.URLError):
                if attempt == 2:
                    raise
                time.sleep(0.5 * (attempt + 1))
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(source, encoding="utf-8")
        return source


def preprocess(source: str) -> str:
    """Use a system C preprocessor or the small optional pcpp package."""
    cpp = shutil.which("cpp")
    if cpp is not None:
        result = subprocess.run(
            [cpp, "-P", "-x", "c++", "-"],
            input=source,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        return result.stdout
    try:
        from pcpp import Preprocessor
    except ImportError as exc:
        raise RuntimeError(
            "profile generation requires either cpp on PATH or Python package pcpp"
        ) from exc
    processor = Preprocessor()
    processor.line_directive = None
    processor.parse(source)
    output = io.StringIO()
    processor.write(output)
    return output.getvalue()


def macro_body(source: str, name: str) -> str:
    lines = source.splitlines()
    prefix = f"#define {name}("
    for index, line in enumerate(lines):
        if not line.startswith(prefix):
            continue
        body: list[str] = []
        while index < len(lines):
            current = lines[index]
            body.append(current[:-1] if current.endswith("\\") else current)
            index += 1
            if not current.endswith("\\"):
                break
        return "\n".join(body[1:])
    raise ValueError(f"macro {name} not found")


def split_arguments(value: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(value):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    parts.append(value[start:].strip())
    return parts


def macro_calls(body: str) -> list[list[str]]:
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    calls: list[list[str]] = []
    for match in re.finditer(r"\bV(?:_TSA)?\s*\(", body):
        start = match.end()
        depth = 1
        index = start
        while index < len(body) and depth:
            if body[index] == "(":
                depth += 1
            elif body[index] == ")":
                depth -= 1
            index += 1
        if depth:
            raise ValueError("unterminated macro call")
        calls.append(split_arguments(body[start : index - 1]))
    return calls


def expand_name_macro(source: str, name: str) -> set[str]:
    source = re.sub(r"^\s*#\s*include[^\n]*\n", "", source, flags=re.MULTILINE)
    expansion = f"""
OFFLINE_NAMES_BEGIN
#define OFFLINE_NAME(name) OFFLINE_NAME_ENTRY(name)
{name}(OFFLINE_NAME)
OFFLINE_NAMES_END
"""
    result = preprocess(source + expansion)
    section = result.split("OFFLINE_NAMES_BEGIN", 1)[1].split(
        "OFFLINE_NAMES_END", 1
    )[0]
    return set(re.findall(r"OFFLINE_NAME_ENTRY\((\w+)\)", section))


def parse_bytecodes(source: str) -> list[dict[str, object]]:
    unique_name = (
        "BYTECODE_LIST_WITH_UNIQUE_HANDLERS_IMPL"
        if "#define BYTECODE_LIST_WITH_UNIQUE_HANDLERS_IMPL(" in source
        else "BYTECODE_LIST_WITH_UNIQUE_HANDLERS"
    )
    calls = macro_calls(macro_body(source, unique_name))
    calls.extend(macro_calls(macro_body(source, "SHORT_STAR_BYTECODE_LIST")))
    calls.append(["Illegal", "ImplicitRegisterUse::kNone"])

    immediate_jumps = expand_name_macro(source, "JUMP_IMMEDIATE_BYTECODE_LIST")
    constant_jumps = expand_name_macro(source, "JUMP_CONSTANT_BYTECODE_LIST")
    forward_jumps = expand_name_macro(source, "JUMP_FORWARD_BYTECODE_LIST")
    bytecodes: list[dict[str, object]] = []
    for opcode, args in enumerate(calls):
        name = args[0]
        operands = [arg.removeprefix("OperandType::k") for arg in args[2:]]
        jump_mode = None
        if name == "JumpLoop":
            jump_mode = "backward_immediate"
        elif name in forward_jumps and name in immediate_jumps:
            jump_mode = "forward_immediate"
        elif name in forward_jumps and name in constant_jumps:
            jump_mode = "forward_constant"
        bytecodes.append(
            {
                "opcode": opcode,
                "name": name,
                "operands": operands,
                "jump_mode": jump_mode,
            }
        )
    if len(bytecodes) > 256:
        raise ValueError(f"too many bytecodes: {len(bytecodes)}")
    return bytecodes


def parse_serializer_tags(source: str) -> dict[str, int]:
    match = re.search(r"enum Bytecode\s*:\s*(?:byte|uint8_t)\s*\{(.*?)\n\s*\};", source, re.DOTALL)
    if not match:
        raise ValueError("serializer bytecode enum not found")
    body = re.sub(r"/\*.*?\*/|//[^\n]*", "", match.group(1), flags=re.DOTALL)
    tags: dict[str, int] = {}
    current = -1
    for item in body.split(","):
        item = item.strip()
        if not item or not item.startswith("k"):
            continue
        if "=" in item:
            name, raw_value = (part.strip() for part in item.split("=", 1))
            current = int(raw_value, 0)
        else:
            name = item
            current += 1
        tags[name.removeprefix("k")] = current
    return tags


def parse_bytecode_array_layout(source: str) -> dict[str, object]:
    match = re.search(
        r"extern class BytecodeArray extends \w+\s*\{(.*?)\n\}", source, re.DOTALL
    )
    if not match:
        raise ValueError("BytecodeArray torque definition not found")
    body = re.sub(r"/\*.*?\*/|//[^\n]*", "", match.group(1), flags=re.DOTALL)
    fields = re.findall(r"(?:const\s+)?(\w+)\s*:\s*([^;]+);", body)
    types = {name: field_type.strip() for name, field_type in fields}
    names = [name for name, _ in fields]
    if "length" not in names:
        names.insert(0, "length")
    length_index = names.index("length")

    def delta(name: str) -> int:
        return names.index(name) - length_index

    parameter_type = types["parameter_size"]
    return {
        "constant_pool_slot_delta": delta("constant_pool"),
        "handler_table_slot_delta": delta("handler_table"),
        "source_position_table_slot_delta": delta("source_position_table"),
        "frame_size_slot_delta": delta("frame_size"),
        "parameter_encoding": "count_u16" if "uint16" in parameter_type else "size_i32",
    }


def parse_shared_function_info_layout(source: str) -> dict[str, list[int]]:
    match = re.search(
        r"(?:extern\s+)?class SharedFunctionInfo extends \w+\s*\{(.*?)\n\}",
        source,
        re.DOTALL,
    )
    if not match:
        raise ValueError("SharedFunctionInfo torque definition not found")
    body = re.sub(r"/\*.*?\*/|//[^\n]*", "", match.group(1), flags=re.DOTALL)
    tagged_fields = re.findall(
        r"(?:@\w+(?:\([^\n]*\))?\s*)*(\w+)\s*:\s*"
        r"(?:Object|String[^;]*|HeapObject|Script[^;]*|TrustedPointer<[^;]+>);",
        body,
    )
    try:
        if "@if(V8_ENABLE_SANDBOX)" in body and "trusted_function_data" in body:
            return {
                "function_data_slots": [1, 2],
                "name_or_scope_info_slots": [2, 3],
            }
        function_data_name = (
            "trusted_function_data"
            if "trusted_function_data" in tagged_fields
            else "function_data"
        )
        return {
            "function_data_slots": [tagged_fields.index(function_data_name) + 1],
            "name_or_scope_info_slots": [tagged_fields.index("name_or_scope_info") + 1],
        }
    except ValueError as exc:
        raise ValueError("SharedFunctionInfo tagged fields not found") from exc


def parse_scope_info_layout(source: str, globals_source: str) -> dict[str, object]:
    flags_match = re.search(
        r"bitfield struct ScopeFlags extends uint(?:31|32)\s*\{(.*?)\n\}",
        source,
        re.DOTALL,
    )
    class_match = re.search(
        r"(?:extern\s+)?class ScopeInfo extends HeapObject\s*\{(.*?)\n\}",
        source,
        re.DOTALL,
    )
    enum_match = re.search(
        r"extern enum ScopeType extends uint32\s*\{(.*?)\n\}",
        source,
        re.DOTALL,
    )
    max_names_match = re.search(
        r"kScopeInfoMaxInlinedLocalNamesSize\s*=\s*(\d+)",
        re.sub(r"//[^\n]*", "", globals_source),
    )
    if not all((flags_match, class_match, enum_match)):
        raise ValueError("ScopeInfo layout metadata not found")

    flags_body = re.sub(
        r"/\*.*?\*/|//[^\n]*", "", flags_match.group(1), flags=re.DOTALL
    )
    shifts: dict[str, int] = {}
    shift = 0
    for name, width in re.findall(r"(\w+)\s*:[^;:]+:\s*(\d+)\s+bit;", flags_body):
        shifts[name] = shift
        shift += int(width)

    class_body = re.sub(
        r"/\*.*?\*/|//[^\n]*", "", class_match.group(1), flags=re.DOTALL
    )
    enum_body = re.sub(
        r"/\*.*?\*/|//[^\n]*", "", enum_match.group(1), flags=re.DOTALL
    )
    scope_types = [item.strip() for item in enum_body.split(",") if item.strip()]
    module_count = class_body.find("module_variable_count")
    local_names = class_body.find("context_local_names[")
    saved_class_flag = (
        "has_saved_class_variable"
        if "has_saved_class_variable" in shifts
        else "has_saved_class_variable_index"
    )
    required_flags = (
        saved_class_flag,
        "function_variable",
        "has_inferred_function_name",
    )
    if any(name not in shifts for name in required_flags):
        raise ValueError("required ScopeFlags fields not found")
    return {
        "flags_encoding": (
            "smi" if "flags: SmiTagged<ScopeFlags>" in class_body else "uint32"
        ),
        "variable_part_slot": (
            6 if re.search(r"position_info\s*:\s*PositionInfo", class_body) else 4
        ),
        "module_count_before_locals": 0 <= module_count < local_names,
        "module_scope_value": scope_types.index("MODULE_SCOPE"),
        # Before the out-of-line local-name representation was introduced,
        # the Torque layout stores context_local_names inline unconditionally.
        "max_inlined_local_names": (
            int(max_names_match.group(1)) if max_names_match else (1 << 31) - 1
        ),
        "scope_type_shift": shifts["scope_type"],
        "scope_type_mask": 0xF,
        "saved_class_variable_bit": shifts[saved_class_flag],
        "function_variable_shift": shifts["function_variable"],
        "function_variable_mask": 0x3,
        "inferred_function_name_bit": shifts["has_inferred_function_name"],
    }


def parse_runtime_names(source: str, leaptiering: bool) -> list[str]:
    source = re.sub(r"^\s*#\s*include[^\n]*\n", "", source, flags=re.MULTILINE)
    definitions = [
        "#define V8_INTL_SUPPORT 1",
        "#define V8_ENABLE_WEBASSEMBLY 1",
        "#define IF_WASM(V, ...) V(__VA_ARGS__)",
        "#define IF_WASM_DRUMBRAKE(V, ...)",
        "#define IF_V8_WASM_RANDOM_FUZZERS(V, ...)",
        "#define NOTHING(...)",
    ]
    if leaptiering:
        definitions.append("#define V8_ENABLE_LEAPTIERING 1")
    expansion = """
OFFLINE_RUNTIME_BEGIN
#define OFFLINE_F(name, nargs, ressize, ...) OFFLINE_RUNTIME(name)
#define OFFLINE_I(name, nargs, ressize, ...) OFFLINE_RUNTIME(name)
FOR_EACH_INTRINSIC(OFFLINE_F)
FOR_EACH_INLINE_INTRINSIC(OFFLINE_I)
OFFLINE_RUNTIME_END
"""
    result = preprocess("\n".join(definitions) + "\n" + source + expansion)
    section = result.split("OFFLINE_RUNTIME_BEGIN", 1)[1].split(
        "OFFLINE_RUNTIME_END", 1
    )[0]
    return re.findall(r"OFFLINE_RUNTIME\((\w+)\)", section)


def parse_intrinsic_names(source: str) -> list[str]:
    return [call[0] for call in macro_calls(macro_body(source, "INTRINSICS_LIST"))]


def parse_root_metadata(
    heap_symbols_source: str,
    accessors_source: str,
    roots_source: str,
    static_roots_source: str | None,
) -> tuple[list[str], dict[str, str], dict[str, str]]:
    source = "\n".join((heap_symbols_source, accessors_source, roots_source))
    source = re.sub(r"^\s*#\s*include[^\n]*\n", "", source, flags=re.MULTILINE)
    definitions = """
#define V8_INTL_SUPPORT 1
#define V8_ENABLE_WEBASSEMBLY 1
#define IF_WASM(V, ...) V(__VA_ARGS__)
"""
    expansion = """
OFFLINE_ROOT_BEGIN
#define OFFLINE_ROOT(type, name, CamelName) OFFLINE_ROOT_ENTRY(name)
ROOT_LIST(OFFLINE_ROOT)
OFFLINE_ROOT_END
OFFLINE_MUTABLE_ROOT_BEGIN
MUTABLE_ROOT_LIST(OFFLINE_ROOT)
OFFLINE_MUTABLE_ROOT_END
OFFLINE_STRING_BEGIN
#define OFFLINE_STRING(_, name, value) OFFLINE_STRING_ENTRY(name, value)
INTERNALIZED_STRING_LIST_GENERATOR(OFFLINE_STRING, ignored)
INTERNALIZED_STRING_FOR_PROTECTOR_LIST_GENERATOR(OFFLINE_STRING, ignored)
OFFLINE_STRING_END
"""
    result = preprocess(definitions + source + expansion)
    root_section = result.split("OFFLINE_ROOT_BEGIN", 1)[1].split(
        "OFFLINE_ROOT_END", 1
    )[0]
    root_names = re.findall(r"OFFLINE_ROOT_ENTRY\((\w+)\)", root_section)
    mutable_section = result.split("OFFLINE_MUTABLE_ROOT_BEGIN", 1)[1].split(
        "OFFLINE_MUTABLE_ROOT_END", 1
    )[0]
    mutable_root_names = re.findall(r"OFFLINE_ROOT_ENTRY\((\w+)\)", mutable_section)
    string_section = result.split("OFFLINE_STRING_BEGIN", 1)[1].split(
        "OFFLINE_STRING_END", 1
    )[0]
    strings_by_name: dict[str, str] = {}
    for name, literal in re.findall(
        r"OFFLINE_STRING_ENTRY\((\w+),\s*((?:\"(?:\\.|[^\"])*\"\s*)+)\)",
        string_section,
    ):
        pieces = re.findall(r'"(?:\\.|[^\"])*"', literal)
        strings_by_name[name] = "".join(ast.literal_eval(piece) for piece in pieces)
    strings_by_name.setdefault("empty_string", "")
    read_only_strings: dict[str, str] = {}
    static_root_maps: dict[str, str] = {}
    static_root_offsets: list[int] = []
    if static_roots_source is not None:
        table_match = re.search(
            r"StaticReadOnlyRootsPointerTable\s*=\s*\{(.*?)\n\};",
            static_roots_source,
            re.DOTALL,
        )
        if table_match:
            read_only_root_names = re.findall(
                r"StaticReadOnlyRoot::k(\w+)", table_match.group(1)
            )
            root_names = read_only_root_names + mutable_root_names
        for name, raw_offset in re.findall(
            r"static constexpr Tagged_t k(\w+)\s*=\s*(0x[0-9a-fA-F]+);",
            static_roots_source,
        ):
            pointer = int(raw_offset, 16)
            static_root_offsets.append(pointer - 1)
            if name.endswith("StringMap"):
                static_root_maps[str(pointer)] = name
            if name in strings_by_name:
                read_only_strings[str(pointer - 1)] = strings_by_name[name]
    root_strings = {
        str(index): strings_by_name[name]
        for index, name in enumerate(root_names)
        if name in strings_by_name
    }
    static_root_area_start = min(static_root_offsets) if static_root_offsets else None
    return (
        root_names,
        root_strings,
        read_only_strings,
        static_root_maps,
        static_root_area_start,
    )


_MASK_32 = (1 << 32) - 1
_MASK_64 = (1 << 64) - 1


def _hash_unsigned_32(value: int) -> int:
    value = (~value + (value << 15)) & _MASK_32
    value ^= value >> 12
    value = (value + (value << 2)) & _MASK_32
    value ^= value >> 4
    value = (value * 2057) & _MASK_32
    value ^= value >> 16
    return value


def _hash_combine_64(seed: int, value: int) -> int:
    multiplier = 0xC6A4A7935BD1E995
    value = (value * multiplier) & _MASK_64
    value ^= value >> 47
    value = (value * multiplier) & _MASK_64
    return ((seed ^ value) * multiplier) & _MASK_64


def calculate_version_hash(version: str, functional_source: str) -> int:
    """Reproduce Version::Hash for the implementation in this exact V8 tag."""
    parts = [int(value) for value in version.split(".")]
    if len(parts) == 3:
        parts.append(0)
    if len(parts) != 4:
        raise ValueError(f"V8 version must contain three or four numeric fields: {version}")
    values = [_hash_unsigned_32(value) for value in parts]
    if "class Hasher" in functional_source:
        order = values
    elif "hash_combine(hash_combine(vs...), hash<T>()(v))" in functional_source:
        order = reversed(values)
    else:
        raise ValueError("unrecognized V8 hash_combine implementation")
    seed = 0
    for value in order:
        seed = _hash_combine_64(seed, value)
    return seed & _MASK_32


def parse_object_boilerplate_layout(source: str) -> str:
    """Derive the literal layout from V8 types; reject unknown structures."""
    if "ObjectBoilerplateDescriptionShape" in source or re.search(
        r"class\s+ObjectBoilerplateDescription\s*\n?\s*"
        r":\s*public\s+TaggedArrayBase<",
        source,
    ):
        return "tagged_array"
    if re.search(
        r"class\s+ObjectBoilerplateDescription\s*:\s*public\s+FixedArray\b",
        source,
    ):
        return "fixed_array"
    raise ValueError("unrecognized ObjectBoilerplateDescription layout")


def build_profile(provider: SourceProvider, version: str) -> dict[str, object]:
    bytecodes_source = provider.read(version, "src/interpreter/bytecodes.h")
    serializer_source = provider.read(version, "src/snapshot/serializer-deserializer.h")
    code_serializer_source = provider.read(version, "src/snapshot/code-serializer.h")
    runtime_source = provider.read(version, "src/runtime/runtime.h")
    intrinsics_source = provider.read(version, "src/interpreter/interpreter-intrinsics.h")
    heap_symbols_source = provider.read(version, "src/init/heap-symbols.h")
    accessors_source = provider.read(version, "src/builtins/accessors.h")
    roots_source = provider.read(version, "src/roots/roots.h")
    static_roots_source = provider.read_optional(version, "src/roots/static-roots.h")
    globals_source = provider.read(version, "src/common/globals.h")
    functional_source = provider.read_optional(version, "src/base/functional.h")
    if functional_source is None:
        functional_source = provider.read(version, "src/base/hashing.h")
    literal_objects_source = provider.read(version, "src/objects/literal-objects.h")
    scope_info_source = provider.read(version, "src/objects/scope-info.tq")
    shared_function_info_source = provider.read(
        version, "src/objects/shared-function-info.tq"
    )
    bytecode_array_source = provider.read_optional(version, "src/objects/bytecode-array.tq")
    if bytecode_array_source is None:
        bytecode_array_source = provider.read(version, "src/objects/code.tq")
    tags = parse_serializer_tags(serializer_source)
    legacy_runtime_names = parse_runtime_names(runtime_source, False)
    leaptiering_runtime_names = parse_runtime_names(runtime_source, True)
    default_runtime_variant = (
        "leaptiering"
        if tuple(map(int, version.split(".")[:2])) >= (13, 2)
        else "legacy"
    )
    (
        root_names,
        root_strings,
        read_only_strings,
        static_root_maps,
        static_root_area_start,
    ) = parse_root_metadata(
        heap_symbols_source, accessors_source, roots_source, static_roots_source
    )
    return {
        "version": version,
        "version_hash": calculate_version_hash(version, functional_source),
        "register_file_start": -6 if tuple(map(int, version.split(".")[:2])) < (11, 9) else -7,
        "has_ro_snapshot_checksum": "kReadOnlySnapshotChecksumOffset" in code_serializer_source,
        "serializer_tags": tags,
        "snapshot_spaces": tags["Backref"],
        "bytecode_array_layout": parse_bytecode_array_layout(bytecode_array_source),
        "shared_function_info_layout": parse_shared_function_info_layout(
            shared_function_info_source
        ),
        "scope_info_layout": parse_scope_info_layout(
            scope_info_source, globals_source
        ),
        "runtime_default_variant": default_runtime_variant,
        "runtime_variants": {
            "legacy": legacy_runtime_names,
            "leaptiering": leaptiering_runtime_names,
        },
        "runtime_variant_by_flags_hash": RUNTIME_VARIANT_BY_FLAGS_HASH.get(version, {}),
        "intrinsic_names": parse_intrinsic_names(intrinsics_source),
        "root_names": root_names,
        "root_strings": root_strings,
        "read_only_strings": read_only_strings,
        "read_only_strings_by_tagged_size": {},
        "static_root_maps": static_root_maps,
        "static_root_area_start": static_root_area_start,
        "object_boilerplate_layout": parse_object_boilerplate_layout(
            literal_objects_source
        ),
        "bytecodes": parse_bytecodes(bytecodes_source),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate exact cached-data profiles from V8 source tags"
    )
    parser.add_argument("--source", choices=("github", "git"), default="github")
    parser.add_argument("--v8-repo", type=Path)
    parser.add_argument("--github-repository", default="v8/v8")
    parser.add_argument(
            "--cache-dir", type=Path, default=Path.home() / ".cache" / "v8bytecode-recover"
    )
    parser.add_argument("--version", action="append", dest="versions")
    parser.add_argument("--versions-file", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"profile directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument("--merge-existing", action="store_true")
    args = parser.parse_args()

    versions = list(args.versions or [])
    if args.versions_file:
        versions.extend(
            line.strip()
            for line in args.versions_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    if not versions:
        versions = list(VERSIONS)
    versions = list(dict.fromkeys(versions))
    if args.source == "git":
        if args.v8_repo is None:
            parser.error("--v8-repo is required with --source git")
        provider: SourceProvider = GitRepositoryProvider(args.v8_repo)
    else:
        provider = GitHubRawProvider(args.github_repository, args.cache_dir)

    profiles = [build_profile(provider, version) for version in versions]
    merge_existing = args.merge_existing or (
        args.output_dir.resolve() == DEFAULT_OUTPUT_DIR.resolve()
    )
    if merge_existing and (args.output_dir / "index.json").is_file():
        existing_index = json.loads(
            (args.output_dir / "index.json").read_text(encoding="utf-8")
        )
        existing = set(existing_index.get("versions", [])) - set(versions)
        versions = sorted(
            [*existing, *versions], key=lambda item: tuple(map(int, item.split(".")))
        )
    index = {
        "format": 1,
        "operand_encoding": {
            "scalable_signed": sorted(SCALABLE_SIGNED),
            "scalable_unsigned": sorted(SCALABLE_UNSIGNED),
            "fixed_sizes": FIXED_SIZES,
        },
        "versions": versions,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n"
    )
    for profile in profiles:
        (args.output_dir / f"{profile['version']}.json").write_text(
            json.dumps(profile, indent=2, sort_keys=True) + "\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
