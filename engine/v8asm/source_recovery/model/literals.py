"""Array, object and scope-info literal models."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from .base import V8Address, V8HeapObject, V8Smi
from .arrays import V8FixedArray


def _parse_inline_value(payload: str) -> Any:
    payload = payload.strip()
    if payload.startswith("0x"):
        return V8Address.from_text(payload)
    if payload.lstrip("-").isdigit():
        return V8Smi(int(payload))
    return payload


@dataclass(frozen=True)
class V8ClassProperty:
    placement: str
    kind: str
    value_index: int
    key: Any = None
    computed_key_index: Optional[int] = None


class V8ClassBoilerplate(V8HeapObject):
    def __init__(self, address: int, i_type: str, lines: list[str]):
        super().__init__(address, i_type, lines)
        self.arguments_count: Optional[int] = None
        self.properties: list[V8ClassProperty] = []

    def parse(self):
        for ln in self.raw_asm_lines:
            if "arguments count:" in ln:
                self.arguments_count = int(ln.split(":", 1)[1].strip())
                continue
            match = re.match(
                r"^\s*- (static|instance) (data|getter|setter) "
                r"value=(\d+) (key|computed_key)=(.+)$",
                ln,
            )
            if not match:
                continue
            key = None
            computed_key_index = None
            if match.group(4) == "computed_key":
                computed_key_index = int(match.group(5))
            else:
                key = _parse_inline_value(match.group(5))
            self.properties.append(
                V8ClassProperty(
                    placement=match.group(1),
                    kind=match.group(2),
                    value_index=int(match.group(3)),
                    key=key,
                    computed_key_index=computed_key_index,
                )
            )

    def __repr__(self):
        return (
            f"<V8ClassBoilerplate: 0x{self.address:012x} "
            f"args={self.arguments_count} properties={len(self.properties)}>"
        )


class V8ArrayBoilerplateDescription(V8HeapObject):
    def __init__(self, address: int, i_type: str, lines: list[str]):
        super().__init__(address, i_type, lines)
        self.elements_kind: Optional[str] = None
        self.constant_elements: Optional[V8Address[V8FixedArray]] = None

    def parse(self):
        for ln in self.raw_asm_lines:
            if "elements kind:" in ln:
                self.elements_kind = ln.split(":", 1)[1].strip()
            elif "constant elements:" in ln:
                payload = ln.split(":", 1)[1].strip()
                self.constant_elements = V8Address[V8FixedArray].from_text(payload)

    def __repr__(self):
        return (
            f"<V8ArrayBoilerplateDescription: 0x{self.address:012x} "
            f"kind={self.elements_kind} "
            f"const={self.constant_elements}>"
        )


class V8ObjectBoilerplateDescription(V8HeapObject):
    def __init__(self, address: int, i_type: str, lines: list[str]):
        super().__init__(address, i_type, lines)
        self.capacity: Optional[int] = None
        self.backing_store_size: Optional[int] = None
        self.flags: Optional[int] = None
        self.entries: list[Any] = []

    def parse(self):
        in_elements = False
        for ln in self.raw_asm_lines:
            stripped = ln.strip()
            if "capacity:" in ln:
                self.capacity = int(ln.split(":", 1)[1].strip())
            elif "backing_store_size:" in ln:
                self.backing_store_size = int(ln.split(":", 1)[1].strip())
            elif "flags:" in ln:
                self.flags = int(ln.split(":", 1)[1].strip())
            elif stripped == "- elements:":
                in_elements = True
            elif in_elements:
                match = re.match(r"^(\d+):\s*(.+)$", stripped)
                if not match:
                    continue
                self.entries.append(_parse_inline_value(match.group(2)))

    def __repr__(self):
        return (
            f"<V8ObjectBoilerplateDescription: 0x{self.address:012x} "
            f"capacity={self.capacity} entries={len(self.entries)}>"
        )


class V8ScopeInfo(V8HeapObject):
    def __init__(self, address: int, i_type: str, lines: list[str]):
        super().__init__(address, i_type, lines)
        self.scope_type: Optional[str] = None
        self.context_local_count: Optional[int] = None
        self.context_slots: list[Any] = []

    def parse(self):
        in_context_slots = False
        for ln in self.raw_asm_lines:
            stripped = ln.strip()
            if "scope type:" in ln:
                self.scope_type = ln.split(":", 1)[1].strip()
            elif "context locals :" in ln:
                self.context_local_count = int(ln.split(":", 1)[1].strip())
            elif stripped == "- context slots {":
                in_context_slots = True
            elif in_context_slots and stripped == "}":
                in_context_slots = False
            elif in_context_slots:
                match = re.match(r"^- (\d+):\s*(.+)$", stripped)
                if not match:
                    continue
                self.context_slots.append(_parse_inline_value(match.group(2)))

    def __repr__(self):
        return (
            f"<V8ScopeInfo: 0x{self.address:012x} "
            f"type={self.scope_type} context_slots={len(self.context_slots)}>"
        )
