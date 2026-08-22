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

try:
    from .source_layout import SourceLayout, SourceLayoutError
except ImportError:  # Direct execution from the tooling directory.
    from source_layout import SourceLayout, SourceLayoutError  # type: ignore[no-redef]

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "profiles"


def _load_bundled_versions() -> tuple[str, ...]:
    """Use the reviewed catalog as the single source of default versions."""
    index_path = DEFAULT_OUTPUT_DIR / "index.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"unable to read bundled profile catalog: {index_path}"
        ) from exc

    versions = index.get("versions")
    if not isinstance(versions, list) or not all(
        isinstance(version, str) for version in versions
    ):
        raise ValueError(f"invalid versions in bundled profile catalog: {index_path}")
    return tuple(versions)


VERSIONS = _load_bundled_versions()

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

OPERAND_INFO_NAMES = frozenset(
    {
        "None",
        "ScalableSignedByte",
        "ScalableUnsignedByte",
        "FixedUnsignedByte",
        "FixedUnsignedShort",
    }
)

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


def parse_operand_types(source: str) -> dict[str, str]:
    """Translate V8 OperandTypeInfo entries into decoder operand names.

    V8 periodically renames operands without changing their wire encoding.
    The source-defined primitive type is the reliable part of that contract;
    names that are only descriptive are mapped to the closest canonical type.
    """
    source = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.DOTALL)
    matches = re.findall(
        r"\bV\(\s*(\w+)\s*,\s*"
        r"(?:OperandTypeInfo|OperandType)::k(\w+)",
        source,
    )
    if not matches:
        raise ValueError("bytecode operand type info list not found")

    result: dict[str, str] = {}
    for name, info in matches:
        if info not in OPERAND_INFO_NAMES:
            continue
        if info == "None":
            canonical = "None"
        elif info == "ScalableSignedByte":
            normalized = _normalize_operand(name)
            canonical = normalized if normalized in SCALABLE_SIGNED else "Reg"
        elif info == "ScalableUnsignedByte":
            normalized = _normalize_operand(name)
            canonical = normalized if normalized in SCALABLE_UNSIGNED else "Idx"
        elif info == "FixedUnsignedByte":
            normalized = _normalize_operand(name)
            canonical = (
                normalized
                if normalized in FIXED_SIZES and FIXED_SIZES[normalized] == 1
                else "Flag8"
            )
        else:
            normalized = _normalize_operand(name)
            canonical = (
                normalized
                if normalized in FIXED_SIZES and FIXED_SIZES[normalized] == 2
                else "Flag16"
            )

        previous = result.get(name)
        if previous is not None and previous != canonical:
            raise ValueError(
                f"operand {name} has conflicting source encodings: "
                f"{previous} and {canonical}"
            )
        result[name] = canonical
    if not result:
        raise ValueError("bytecode operand type info list has no supported entries")
    return result


def parse_bytecodes(
    source: str, operand_types: dict[str, str]
) -> list[dict[str, object]]:
    unique_name = next(
        (
            name
            for name in (
                "BYTECODE_LIST_WITH_UNIQUE_HANDLERS_IMPL",
                "BYTECODE_LIST_WITH_UNIQUE_HANDLERS",
                "BYTECODE_LIST",
            )
            if f"#define {name}(" in source
        ),
        None,
    )
    if unique_name is None:
        raise ValueError("interpreter bytecode list macro not found")
    calls = macro_calls(macro_body(source, unique_name))
    if "#define SHORT_STAR_BYTECODE_LIST(" in source:
        calls.extend(macro_calls(macro_body(source, "SHORT_STAR_BYTECODE_LIST")))
    calls.append(["Illegal", "AccumulatorUse::kNone"])

    def names(name: str) -> set[str]:
        return expand_name_macro(source, name) if f"#define {name}(" in source else set()

    immediate_jumps = names("JUMP_IMMEDIATE_BYTECODE_LIST")
    constant_jumps = names("JUMP_CONSTANT_BYTECODE_LIST")
    forward_jumps = names("JUMP_FORWARD_BYTECODE_LIST")
    bytecodes: list[dict[str, object]] = []
    for opcode, args in enumerate(calls):
        if not args:
            continue
        name = args[0]
        operand_start = (
            2
            if len(args) > 1 and args[1].startswith(
                ("AccumulatorUse::", "ImplicitRegisterUse::")
            )
            else 1
        )
        operands = []
        for arg in args[operand_start:]:
            raw_operand = arg.removeprefix("OperandType::k")
            if raw_operand == "None":
                continue
            operands.append(_normalize_operand(raw_operand, operand_types))
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


def _normalize_operand(
    operand: str, operand_types: dict[str, str] | None = None
) -> str:
    """Map pre-Torque width-suffixed operands to the shared decoder model."""
    for base in (
        "RegOutTriple",
        "RegOutPair",
        "RegOutList",
        "RegInOut",
        "RegCount",
        "MaybeReg",
        "RegPair",
        "RegList",
        "RegOut",
        "Reg",
        "Idx",
        "Imm",
    ):
        if operand.startswith(base) and operand[len(base) :].isdigit():
            operand = "Reg" if base == "MaybeReg" else base
            break
    if operand_types is not None:
        try:
            return operand_types[operand]
        except KeyError as exc:
            raise ValueError(
                f"operand {operand} is missing from the source operand contract"
            ) from exc
    return operand


def _parse_int_expression(value: str, constants: dict[str, int]) -> int | None:
    value = value.strip()
    try:
        return int(value, 0)
    except ValueError:
        pass
    match = re.fullmatch(r"(k\w+)\s*([+-])\s*(\d+)", value)
    if match and match.group(1) in constants:
        delta = int(match.group(3))
        return constants[match.group(1)] + (delta if match.group(2) == "+" else -delta)
    return constants.get(value)


def _parse_enum_constants(source: str, enum_names: tuple[str, ...]) -> dict[str, int]:
    constants: dict[str, int] = {}
    for enum_name in enum_names:
        match = re.search(
            rf"\benum\s+(?:class\s+)?{enum_name}\b(?:\s*:\s*[\w:]+)?\s*\{{(.*?)\}}\s*;",
            source,
            re.DOTALL,
        )
        if not match:
            continue
        current = -1
        body = re.sub(r"/\*.*?\*/|//[^\n]*", "", match.group(1), flags=re.DOTALL)
        for item in body.split(","):
            item = item.strip()
            if not item or not re.match(r"k\w+", item):
                continue
            if "=" in item:
                name, raw = (part.strip() for part in item.split("=", 1))
                parsed = _parse_int_expression(raw, constants)
                if parsed is None:
                    continue
                current = parsed
            else:
                name = item.split()[0]
                current += 1
            constants[name] = current
    for match in re.finditer(
        r"(?:static\s+)?(?:constexpr\s+)?(?:int|uint(?:8|16|32)_t)\s+"
        r"(k\w+)\s*=\s*([^;]+);",
        source,
    ):
        value = _parse_int_expression(match.group(2), constants)
        if value is not None:
            constants.setdefault(match.group(1), value)
    return constants


def parse_serializer_tags(source: str) -> dict[str, int]:
    constants = _parse_enum_constants(source, ("Bytecode", "Where"))
    tags = {name.removeprefix("k"): value for name, value in constants.items()}
    required = (
        "NewObject",
        "Backref",
        "RootArray",
        "RootArrayConstants",
        "FixedRawData",
        "VariableRawData",
        "HotObject",
        "Nop",
        "Synchronize",
    )
    missing = [name for name in required if name not in tags]
    if missing:
        raise ValueError(
            "serializer tag constants not found: " + ", ".join(missing)
        )
    return tags


def _evaluate_header_expression(expression: str, constants: dict[str, int]) -> int | None:
    """Evaluate the small integer expressions used by SerializedCodeData offsets."""
    expression = re.sub(r"static_cast<[^>]+>\s*", "", expression)
    expression = re.sub(r"POINTER_SIZE_ALIGN\s*\(([^()]*)\)", r"(\1)", expression)
    expression = expression.replace("kInt32Size", "4").replace("kUInt32Size", "4")
    names = set(re.findall(r"\bk[A-Za-z0-9_]+\b", expression))
    if any(name not in constants for name in names):
        return None
    for name in names:
        expression = re.sub(rf"\b{re.escape(name)}\b", str(constants[name]), expression)
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError:
        return None

    def visit(node: ast.AST) -> int:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.LShift, ast.RShift)
        ):
            left = visit(node.left)
            right = visit(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.FloorDiv):
                return left // right
            if isinstance(node.op, ast.LShift):
                return left << right
            return left >> right
        raise ValueError("unsupported header offset expression")

    try:
        return visit(tree)
    except (ValueError, ZeroDivisionError):
        return None


def _header_constants(source: str) -> tuple[dict[str, int], bool]:
    declarations = re.findall(
        r"\bstatic\s+(?:constexpr\s+)?(?:const\s+)?"
        r"(?:int|uint(?:8|16|32|64)_t|size_t)\s+"
        r"(k\w+)\s*=\s*([^;]+);",
        source,
    )
    # SerializedData owns kMagicNumberOffset, while code-serializer headers
    # refer to it without including its declaration in every source layout.
    constants: dict[str, int] = {
        "kMagicNumberOffset": 0,
        "kInt32Size": 4,
        "kUInt32Size": 4,
    }
    pending = list(declarations)
    for _ in range(len(pending) + 1):
        progress = False
        remaining: list[tuple[str, str]] = []
        for name, expression in pending:
            value = _evaluate_header_expression(expression, constants)
            if value is None:
                remaining.append((name, expression))
                continue
            constants.setdefault(name, value)
            progress = True
        pending = remaining
        if not progress:
            break
    pointer_aligned = bool(
        re.search(r"kHeaderSize\s*=\s*POINTER_SIZE_ALIGN\s*\(", source)
    )
    return constants, pointer_aligned


def parse_cache_header_layout(
    code_serializer_source: str, serializer_source: str
) -> dict[str, object]:
    """Read the exact SerializedCodeData offsets from the V8 source contract."""
    source = f"{code_serializer_source}\n{serializer_source}"
    constants, pointer_aligned = _header_constants(source)

    def offset(name: str, required: bool = False) -> int | None:
        value = constants.get(name)
        if required and value is None:
            raise ValueError(f"cached-data header constant {name} was not found")
        return value

    version_offset = offset("kVersionHashOffset", True)
    source_offset = offset("kSourceHashOffset", True)
    flags_offset = offset("kFlagHashOffset", True)
    payload_offset = offset("kPayloadLengthOffset", True)
    base_size = offset("kUnalignedHeaderSize")
    if base_size is None:
        base_size = offset("kHeaderSize", True)

    checksum_offsets: list[int] = []
    for name in (
        "kChecksumOffset",
        "kChecksum1Offset",
        "kChecksumPartAOffset",
        "kChecksum2Offset",
        "kChecksumPartBOffset",
    ):
        value = offset(name)
        if value is not None and value not in checksum_offsets:
            checksum_offsets.append(value)
    if not checksum_offsets:
        raise ValueError("cached-data checksum offsets were not found")
    if len(checksum_offsets) == 1 and "kChecksumOffset" not in constants:
        raise ValueError("cached-data header checksum pair is incomplete")

    cpu_features_offset = offset("kCpuFeaturesOffset")
    reservation_count_offset = offset("kNumReservationsOffset")
    code_stub_key_count_offset = offset("kNumCodeStubKeysOffset")
    ro_snapshot_offset = offset("kReadOnlySnapshotChecksumOffset")
    external_reference_offset = offset("kExtraExternalReferencesOffset")
    if external_reference_offset is None and version_offset >= 8:
        # V8 6.x moved version/source fields by one uint32 for the API-provided
        # external reference count without naming that slot in the class.
        external_reference_offset = 4

    if ro_snapshot_offset is not None:
        name = "read-only-checksum"
    elif reservation_count_offset is None:
        name = "legacy"
    elif cpu_features_offset is not None and code_stub_key_count_offset is not None:
        name = "legacy-code-stub-extra" if version_offset >= 8 else "legacy-code-stub"
    elif len(checksum_offsets) > 1:
        name = "reservation-checksum-pair"
    else:
        name = "reservation-checksum"

    return {
        "name": name,
        "base_size": base_size,
        "alignment": 8 if pointer_aligned else 4,
        "version_offset": version_offset,
        "source_offset": source_offset,
        "flags_offset": flags_offset,
        "payload_offset": payload_offset,
        "checksum_offsets": checksum_offsets,
        "cpu_features_offset": cpu_features_offset,
        "ro_snapshot_offset": ro_snapshot_offset,
        "reservation_count_offset": reservation_count_offset,
        "code_stub_key_count_offset": code_stub_key_count_offset,
        "external_reference_count_offset": external_reference_offset,
    }


def parse_cache_header_format(
    code_serializer_source: str, serializer_source: str
) -> str:
    return str(parse_cache_header_layout(code_serializer_source, serializer_source)["name"])


def parse_snapshot_spaces(source: str, tags: dict[str, int]) -> int:
    match = re.search(r"kNumberOfSpaces\s*=\s*(\d+)", source)
    if match:
        return int(match.group(1))
    return tags["Backref"]


def parse_bytecode_array_layout(source: str) -> dict[str, object]:
    match = re.search(
        r"extern class BytecodeArray extends \w+\s*\{(.*?)\n\}", source, re.DOTALL
    )
    if not match:
        constants = {
            name: value
            for name, value in re.findall(
                r"k(ConstantPool|HandlerTable|SourcePositionTable|FrameSize|ParameterSize)"
                r"Offset\s*=\s*([^;]+);",
                source,
            )
        }
        if len(constants) == 5:
            return {
                "constant_pool_slot_delta": 1,
                "handler_table_slot_delta": 2,
                "source_position_table_slot_delta": 3,
                "frame_size_slot_delta": 4,
                "parameter_encoding": "size_i32",
            }
        if "class BytecodeArray" in source and "kHeaderSize" in source:
            # V8 7.x generated these offsets from a C++ class description.
            # The field order is part of the source contract and is verified
            # again when the generated profile is used against real blobs.
            return {
                "constant_pool_slot_delta": 1,
                "handler_table_slot_delta": 2,
                "source_position_table_slot_delta": 3,
                "frame_size_slot_delta": 4,
                "parameter_encoding": "size_i32",
            }
        raise ValueError("BytecodeArray layout definition not found")
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
        macro = re.search(
            r"#define\s+SHARED_FUNCTION_INFO_FIELDS\(V\)(.*?)(?=\n#undef)",
            source,
            re.DOTALL,
        )
        if macro:
            fields = re.findall(r"\bV\(\s*(k\w+Offset)\b", macro.group(1))
            slots = {name: index + 1 for index, name in enumerate(fields)}
            if "kFunctionDataOffset" in slots and "kNameOffset" in slots:
                names = [slots["kNameOffset"]]
                if "kScopeInfoOffset" in slots:
                    names.append(slots["kScopeInfoOffset"])
                return {
                    "function_data_slots": [slots["kFunctionDataOffset"]],
                    "name_or_scope_info_slots": names,
                }

        class_start = re.search(
            r"class\s+SharedFunctionInfo\s*:\s*public\s+HeapObject\s*\{",
            source,
        )
        if class_start:
            layout_match = re.search(
                r"// Layout description\.\s*\n", source[class_start.end() :]
            )
            layout_start = (
                class_start.end() + layout_match.start()
                if layout_match
                else -1
            )
            if layout_start >= 0:
                end_markers = [
                    marker
                    for marker in (
                        source.find("// Bit fields", layout_start),
                        source.find("\n};", layout_start),
                    )
                    if marker >= 0
                ]
                layout_end = min(end_markers) if end_markers else len(source)
                layout = source[layout_start:layout_end]
                fields = re.findall(
                    r"\bstatic\s+const\s+int\s+(k\w+Offset)\s*=", layout
                )
                slots = {name: index + 1 for index, name in enumerate(fields)}
                if "kFunctionDataOffset" in slots and "kNameOffset" in slots:
                    names = [slots["kNameOffset"]]
                    if "kScopeInfoOffset" in slots:
                        names.append(slots["kScopeInfoOffset"])
                    return {
                        "function_data_slots": [slots["kFunctionDataOffset"]],
                        "name_or_scope_info_slots": names,
                    }

        if "class SharedFunctionInfo" in source and "function_data" in source:
            if "name_or_scope_info" in source:
                # V8 7.x keeps the generated field definition outside the tag.
                # The public class contract fixes these two serialized fields
                # immediately after the map; keep this adapter explicit.
                return {"function_data_slots": [1], "name_or_scope_info_slots": [2]}
            raise ValueError(
                "unsupported SharedFunctionInfo C++ layout; field offsets were not found"
            )
        raise ValueError("SharedFunctionInfo layout definition not found")
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


def _parse_torque_scope_flags(source: str) -> dict[str, int]:
    match = re.search(
        r"bitfield\s+struct\s+ScopeFlags\s+extends\s+uint(?:31|32)\s*"
        r"\{(.*?)\n\}",
        source,
        re.DOTALL,
    )
    if not match:
        return {}
    body = re.sub(r"/\*.*?\*/|//[^\n]*", "", match.group(1), flags=re.DOTALL)
    shifts: dict[str, int] = {}
    shift = 0
    for name, width in re.findall(r"(\w+)\s*:[^;:]+:\s*(\d+)\s+bit;", body):
        shifts[name] = shift
        shift += int(width)
    return shifts


def parse_scope_info_layout(
    source: str,
    globals_source: str,
    implementation_source: str | None = None,
) -> dict[str, object]:
    # V8 8.1 keeps the fixed-array contract in scope-info.h while its flags
    # are generated from scope-info.tq. Parse both files as one source unit.
    if implementation_source and implementation_source != source:
        source = f"{source}\n{implementation_source}"
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
        return parse_legacy_scope_info_layout(source, globals_source)

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
        "flags_slot": 1,
        "context_count_slot": 3,
    }


def parse_legacy_scope_info_layout(source: str, globals_source: str) -> dict[str, object]:
    """Read the fixed-array ScopeInfo contract used by pre-Torque V8."""
    numeric_match = re.search(
        r"#define\s+FOR_EACH_SCOPE_INFO_NUMERIC_FIELD\(V\)(.*?)(?=\n\s*#define|\n\s*enum|$)",
        source,
        re.DOTALL,
    )
    if not numeric_match:
        raise ValueError("legacy ScopeInfo numeric fields not found")
    numeric_fields = re.findall(r"\bV\((\w+)\)", numeric_match.group(1))
    if not numeric_fields:
        raise ValueError("legacy ScopeInfo numeric fields are empty")

    flags_start = source.find("// Properties of scopes.")
    flags_end = source.find("// Properties of variables.", flags_start)
    if flags_start < 0:
        flags_start = source.find("class ScopeTypeField")
    flags_source = source[flags_start : flags_end if flags_end >= 0 else None]
    if flags_start < 0:
        flags_source = source
    shifts: dict[str, int] = {}
    widths: dict[str, int] = {}
    next_shift = 0
    for match in re.finditer(
        r"(?:class|struct)\s+(\w+)\s*:\s*public\s+"
        r"(?:(?:\w+::)?BitField)<\s*[^,]+,\s*"
        r"([^,]+),\s*(\d+)\s*>",
        flags_source,
        re.DOTALL,
    ):
        name, raw_shift, raw_width = match.groups()
        shift_match = re.fullmatch(r"(\d+)", raw_shift.strip())
        shift = int(shift_match.group(1)) if shift_match else next_shift
        shifts[name] = shift
        widths[name] = int(raw_width)
        next_shift = shift + widths[name]

    aliases = list(
        re.finditer(
            r"using\s+(\w+)\s*=\s*(?:(?:\w+::)?BitField)<\s*"
            r"[^,]+,\s*(\d+),\s*(\d+)\s*>;",
            flags_source,
            re.DOTALL,
        )
    )
    aliases.extend(
        re.finditer(
            r"using\s+(\w+)\s*=\s*(\w+)::Next<\s*[^,]+,\s*(\d+)\s*>;",
            flags_source,
            re.DOTALL,
        )
    )
    for match in sorted(aliases, key=lambda item: item.start()):
        groups = match.groups()
        if len(groups) == 3 and groups[1].isdigit():
            name, raw_shift, raw_width = groups
            shift = int(raw_shift)
        else:
            name, base, raw_width = groups
            if base not in shifts:
                continue
            shift = shifts[base] + widths[base]
        shifts[name] = shift
        widths[name] = int(raw_width)

    torque_shifts = _parse_torque_scope_flags(source)
    if torque_shifts:
        shifts.update(torque_shifts)

    def first_shift(*names: str) -> int | None:
        for name in names:
            if name in shifts:
                return shifts[name]
        return None

    scope_type_shift = first_shift("ScopeTypeField", "ScopeTypeBits", "scope_type")
    function_variable_shift = first_shift(
        "FunctionVariableField", "FunctionVariableBits", "function_variable"
    )
    if scope_type_shift is None or function_variable_shift is None:
        raise ValueError("legacy ScopeInfo bit fields not found")
    enum_match = re.search(
        r"(?:extern\s+)?enum\s+ScopeType(?:\s*:\s*\w+)?\s*\{(.*?)\}",
        source + "\n" + globals_source,
        re.DOTALL,
    )
    module_value = 3
    if enum_match:
        enum_body = re.sub(
            r"/\*.*?\*/|//[^\n]*", "", enum_match.group(1), flags=re.DOTALL
        )
        enum_values = [item.strip() for item in enum_body.split(",") if item.strip()]
        for index, value in enumerate(enum_values):
            if value.startswith("MODULE_SCOPE"):
                module_value = index
                break
    max_names_match = re.search(
        r"kScopeInfoMaxInlinedLocalNamesSize\s*=\s*(\d+)", globals_source
    )
    context_name = "ContextLocalCount"
    context_index = (
        numeric_fields.index(context_name)
        if context_name in numeric_fields
        else len(numeric_fields) - 1
    )
    saved_bit = first_shift(
        "HasSavedClassVariableIndexField",
        "HasSavedClassVariableIndexBit",
        "has_saved_class_variable_index",
        "HasSavedClassVariableField",
        "has_saved_class_variable",
    )
    inferred_bit = first_shift(
        "HasInferredFunctionNameField",
        "HasInferredFunctionNameBit",
        "has_inferred_function_name",
    )
    return {
        "flags_encoding": "smi",
        "variable_part_slot": len(numeric_fields) + 2,
        "module_count_before_locals": False,
        "module_scope_value": module_value,
        "max_inlined_local_names": (
            int(max_names_match.group(1)) if max_names_match else (1 << 31) - 1
        ),
        "scope_type_shift": scope_type_shift,
        "scope_type_mask": 0xF,
        "saved_class_variable_bit": 31 if saved_bit is None else saved_bit,
        "function_variable_shift": function_variable_shift,
        "function_variable_mask": 0x3,
        "inferred_function_name_bit": 31 if inferred_bit is None else inferred_bit,
        "flags_slot": 2,
        "context_count_slot": context_index + 2,
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
        r"class\s+(?:ObjectBoilerplateDescription|BoilerplateDescription)\s*"
        r":\s*public\s+FixedArray\b",
        source,
    ):
        return "fixed_array"
    if "BoilerplateDescription" not in source and "ObjectBoilerplateDescription" not in source:
        # V8 5.x materializes object literals directly; no special tagged
        # boilerplate object is present in the serialized object model.
        return "fixed_array"
    raise ValueError("unrecognized ObjectBoilerplateDescription layout")


def build_profile(provider: SourceProvider, version: str) -> dict[str, object]:
    layout = SourceLayout.detect(provider, version)
    bytecodes_source = layout.read(provider, version, "bytecodes")
    bytecode_operands_source = layout.read(provider, version, "bytecode_operands")
    serializer_source = layout.read(provider, version, "serializer")
    code_serializer_source = layout.read(provider, version, "code_serializer")
    runtime_source = layout.read(provider, version, "runtime")
    intrinsics_source = layout.read(provider, version, "intrinsics")
    heap_symbols_source = layout.read(provider, version, "heap_symbols")
    accessors_source = layout.read(provider, version, "accessors")
    roots_source = layout.read(provider, version, "roots")
    static_roots_source = layout.read_optional(provider, version, "static_roots")
    globals_source = layout.read(provider, version, "globals")
    functional_source = layout.read(provider, version, "functional")
    literal_objects_source = layout.read(provider, version, "literal_objects")
    scope_info_source = layout.read(provider, version, "scope_info")
    scope_info_header_source = layout.read_optional(
        provider, version, "scope_info_header"
    )
    shared_function_info_source = layout.read(provider, version, "shared_function_info")
    bytecode_array_source = layout.read(provider, version, "bytecode_array")
    tags = parse_serializer_tags(serializer_source)
    cache_header_layout = parse_cache_header_layout(
        code_serializer_source, serializer_source
    )
    header_format = str(cache_header_layout["name"])
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
        "has_ro_snapshot_checksum": cache_header_layout["ro_snapshot_offset"] is not None,
        "header_format": header_format,
        "cache_header_layout": cache_header_layout,
        "serializer_tags": tags,
        "snapshot_spaces": parse_snapshot_spaces(serializer_source, tags),
        "bytecode_array_layout": parse_bytecode_array_layout(bytecode_array_source),
        "shared_function_info_layout": parse_shared_function_info_layout(
            shared_function_info_source
        ),
        "scope_info_layout": parse_scope_info_layout(
            scope_info_source, globals_source, scope_info_header_source
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
        "bytecodes": parse_bytecodes(
            bytecodes_source, parse_operand_types(bytecode_operands_source)
        ),
        "source_layout": layout.name,
    }


def _failure_category(error: BaseException) -> str:
    if isinstance(error, SourceLayoutError):
        return "unsupported-layout"
    if isinstance(error, (urllib.error.HTTPError, urllib.error.URLError, TimeoutError)):
        return "network-error"
    message = str(error).lower()
    if "unsupported" in message or "object model" in message or "layout" in message:
        return "unsupported-object-model"
    return "parse-error"


def _generation_report(
    requested_versions: list[str], results: list[dict[str, object]]
) -> dict[str, object]:
    failures = [item for item in results if item["status"] == "failed"]
    categories: dict[str, int] = {}
    for item in failures:
        category = str(item["category"])
        categories[category] = categories.get(category, 0) + 1
    return {
        "format": 1,
        "tool": "v8bytecode-recover profile generator",
        "requested_versions": requested_versions,
        "results": results,
        "summary": {
            "requested": len(requested_versions),
            "succeeded": len(results) - len(failures),
            "failed": len(failures),
            "failure_categories": categories,
        },
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
    parser.add_argument(
        "--report",
        type=Path,
        help="write a JSON report containing per-version successes and failures",
    )
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

    requested_versions = versions[:]
    profiles: list[dict[str, object]] = []
    report_results: list[dict[str, object]] = []
    for version in requested_versions:
        try:
            profile = build_profile(provider, version)
        except Exception as exc:  # A batch must report the failing version and continue.
            report_results.append(
                {
                    "version": version,
                    "status": "failed",
                    "category": _failure_category(exc),
                    "error": str(exc),
                }
            )
            continue
        profiles.append(profile)
        report_results.append(
            {
                "version": version,
                "status": "success",
                "source_layout": profile.get("source_layout", "unknown"),
                "header_format": profile.get("header_format", "unknown"),
                "version_hash": profile["version_hash"],
            }
        )

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(_generation_report(requested_versions, report_results), indent=2)
            + "\n",
            encoding="utf-8",
        )

    if not profiles:
        return 2

    merge_existing = args.merge_existing or (
        args.output_dir.resolve() == DEFAULT_OUTPUT_DIR.resolve()
    )
    existing_profiles: dict[str, dict[str, object]] = {}
    existing_versions: set[str] = set()
    if merge_existing and (args.output_dir / "index.json").is_file():
        existing_index = json.loads(
            (args.output_dir / "index.json").read_text(encoding="utf-8")
        )
        existing_versions = set(existing_index.get("versions", []))
        for existing_version in existing_versions:
            profile_path = args.output_dir / f"{existing_version}.json"
            if profile_path.is_file():
                existing_profiles[existing_version] = json.loads(
                    profile_path.read_text(encoding="utf-8")
                )
    profile_by_version = {str(profile["version"]): profile for profile in profiles}
    versions = sorted(
        {*existing_versions, *profile_by_version},
        key=lambda item: tuple(map(int, item.split("."))),
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
    all_profiles = {**existing_profiles, **profile_by_version}
    for version in versions:
        profile = all_profiles.get(version)
        if profile is None:
            continue
        (args.output_dir / f"{version}.json").write_text(
            json.dumps(profile, indent=2, sort_keys=True) + "\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
