"""Source-path adapters for V8 profile generation.

V8 moved the same runtime structures between Torque and C++ headers over
time.  Keeping path selection here lets parsers describe the data they need
without pretending that every tag has the same source tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class SourceReader(Protocol):
    def read_optional(self, version: str, path: str) -> str | None:
        ...


class SourceLayoutError(ValueError):
    """A V8 tag does not expose a required source file in a known layout."""

    def __init__(
        self,
        version: str,
        layout: str,
        source_name: str,
        candidates: tuple[str, ...],
    ) -> None:
        self.version = version
        self.layout = layout
        self.source_name = source_name
        self.candidates = candidates
        super().__init__(self._message())

    def _message(self) -> str:
        paths = ", ".join(self.candidates)
        return (
            f"V8 {self.version} source layout {self.layout!r} has no "
            f"{self.source_name} source; tried: {paths}"
        )


@dataclass(frozen=True)
class SourceLayout:
    name: str
    files: dict[str, tuple[str, ...]]

    @classmethod
    def detect(cls, reader: SourceReader, version: str) -> "SourceLayout":
        modern = reader.read_optional(
            version, "src/snapshot/serializer-deserializer.h"
        )
        if modern is not None:
            return cls("modern-torque", MODERN_FILES)

        legacy = reader.read_optional(version, "src/snapshot/serializer-common.h")
        if legacy is not None:
            return cls("legacy-cpp", LEGACY_FILES)

        historical = reader.read_optional(version, "src/snapshot/serializer.h")
        if historical is not None:
            return cls("legacy-cpp", LEGACY_FILES)

        raise SourceLayoutError(
            version,
            "unknown",
            "serializer tags",
            (
                "src/snapshot/serializer-deserializer.h",
                "src/snapshot/serializer-common.h",
                "src/snapshot/serializer.h",
            ),
        )

    def read(self, reader: SourceReader, version: str, source_name: str) -> str:
        candidates = self.files[source_name]
        for path in candidates:
            source = reader.read_optional(version, path)
            if source is not None:
                return source
        raise SourceLayoutError(version, self.name, source_name, candidates)

    def read_optional(
        self, reader: SourceReader, version: str, source_name: str
    ) -> str | None:
        for path in self.files[source_name]:
            source = reader.read_optional(version, path)
            if source is not None:
                return source
        return None


COMMON_FILES = {
    "bytecodes": ("src/interpreter/bytecodes.h",),
    "bytecode_operands": (
        "src/interpreter/bytecode-operands.h",
        "src/interpreter/bytecodes.h",
    ),
    "code_serializer": ("src/snapshot/code-serializer.h",),
    "runtime": ("src/runtime/runtime.h",),
    "intrinsics": (
        "src/interpreter/interpreter-intrinsics.h",
        "src/interpreter/bytecodes.h",
    ),
    "functional": ("src/base/functional.h", "src/base/hashing.h"),
}

MODERN_FILES = {
    **COMMON_FILES,
    "serializer": (
        "src/snapshot/serializer-deserializer.h",
        "src/snapshot/serializer-common.h",
    ),
    "heap_symbols": (
        "src/init/heap-symbols.h",
        "src/heap/heap-symbols.h",
        "src/heap-symbols.h",
    ),
    "accessors": ("src/builtins/accessors.h", "src/accessors.h"),
    "roots": ("src/roots/roots.h", "src/roots.h", "src/heap/heap.h"),
    "globals": ("src/common/globals.h", "src/globals.h"),
    "literal_objects": ("src/objects/literal-objects.h", "src/objects.h"),
    "scope_info": ("src/objects/scope-info.tq", "src/objects/scope-info.h"),
    "scope_info_header": (
        "src/objects/scope-info.h",
        "src/objects.h",
    ),
    "shared_function_info": (
        "src/objects/shared-function-info.tq",
        "src/objects/shared-function-info.h",
        "src/objects.h",
    ),
    "bytecode_array": (
        "src/objects/bytecode-array.tq",
        "src/objects/code.tq",
        "src/objects/code.h",
        "src/objects.h",
    ),
    "registers": (
        "src/interpreter/bytecode-register.h",
        "src/interpreter/bytecodes.h",
    ),
    "static_roots": ("src/roots/static-roots.h",),
}

LEGACY_FILES = {
    **COMMON_FILES,
    "serializer": (
        "src/snapshot/serializer-common.h",
        "src/snapshot/serializer.h",
    ),
    "heap_symbols": (
        "src/heap-symbols.h",
        "src/heap/heap-symbols.h",
        "src/init/heap-symbols.h",
    ),
    "accessors": ("src/accessors.h", "src/builtins/accessors.h"),
    "roots": ("src/roots.h", "src/roots/roots.h", "src/heap/heap.h"),
    "globals": ("src/globals.h", "src/common/globals.h"),
    "literal_objects": ("src/objects/literal-objects.h", "src/objects.h"),
    "scope_info": (
        "src/objects/scope-info.tq",
        "src/objects/scope-info.h",
        "src/objects.h",
    ),
    "scope_info_header": (
        "src/objects/scope-info.h",
        "src/objects.h",
    ),
    "shared_function_info": (
        "src/objects/shared-function-info.h",
        "src/objects.h",
    ),
    "bytecode_array": ("src/objects/code.h", "src/objects.h"),
    "registers": (
        "src/interpreter/bytecode-register.h",
        "src/interpreter/bytecodes.h",
    ),
    "static_roots": (),
}
