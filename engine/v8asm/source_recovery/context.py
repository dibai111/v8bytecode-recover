"""Function, object, scope and capture context used during recovery."""

from __future__ import annotations

from dataclasses import dataclass
from collections import deque
import json
import math
import re
from typing import Any, Dict, Iterable, List, Optional

from .model import (
    V8Address,
    V8ArrayBoilerplateDescription,
    V8BytecodeArray,
    V8ClassBoilerplate,
    V8FixedArray,
    V8HeapObject,
    V8ObjectBoilerplateDescription,
    V8ScopeInfo,
    V8SharedFunctionInfo,
    V8String,
    V8TrustedFixedArray,
    V8Smi,
)

IDENT_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
JS_RESERVED_IDENTIFIERS = frozenset({
    "await", "break", "case", "catch", "class", "const", "continue",
    "debugger", "default", "delete", "do", "else", "enum", "export",
    "extends", "false", "finally", "for", "function", "if", "import",
    "in", "instanceof", "let", "new", "null", "return", "static",
    "super", "switch", "this", "throw", "true", "try", "typeof",
    "var", "void", "while", "with", "yield",
})


def js_binding_identifier(name: Optional[str], fallback: str) -> str:
    if name and IDENT_RE.match(name) and name not in JS_RESERVED_IDENTIFIERS:
        return name
    return fallback


@dataclass
class ConstantPoolEntry:
    index: int
    raw: Any
    display: str


class DecompilerContext:
    """Holds cross-object metadata used during decompilation."""

    def __init__(self, objects: Iterable[V8HeapObject]):
        self.objects: List[V8HeapObject] = list(objects)
        self.by_address: Dict[int, V8HeapObject] = {
            obj.address: obj for obj in self.objects
        }
        self.bytecode_constant_pools: Dict[int, V8TrustedFixedArray] = {}
        self.bytecode_functions: Dict[int, V8SharedFunctionInfo] = {}
        self.synthetic_function_names: Dict[int, str] = {}
        self.captured_scopes: Dict[int, List[Optional[V8ScopeInfo]]] = {}
        self.capture_parents: Dict[int, int] = {}
        self.scope_slot_name_hints: Dict[tuple[int, int], str] = {}
        self._build_indexes()
        self._build_synthetic_function_names()
        self._build_captured_scopes()
        self._build_scope_slot_name_hints()

    def _build_indexes(self) -> None:
        for idx, obj in enumerate(self.objects):
            if isinstance(obj, V8BytecodeArray) and obj.constant_pool_size:
                nxt = self.objects[idx + 1] if idx + 1 < len(self.objects) else None
                if (
                    isinstance(nxt, V8TrustedFixedArray)
                    and nxt.length == obj.constant_pool_size
                ):
                    self.bytecode_constant_pools[obj.address] = nxt

        for obj in self.objects:
            if isinstance(obj, V8SharedFunctionInfo) and obj.trusted_function_data:
                self.bytecode_functions[obj.trusted_function_data.address] = obj

    def _build_synthetic_function_names(self) -> None:
        anonymous_index = 0
        used_names: set[str] = set()
        for obj in self.objects:
            if not isinstance(obj, V8SharedFunctionInfo):
                continue
            raw_name = None
            if obj.name:
                target = self.get_object(obj.name.address)
                raw_name = target.value if isinstance(target, V8String) else None
            if raw_name is None:
                raw_name = getattr(obj, "raw_name", None)
            if not raw_name:
                anonymous_index += 1
                candidate = (
                    "anonymous"
                    if anonymous_index == 1
                    else f"anonymous{anonymous_index}"
                )
            else:
                candidate = js_binding_identifier(
                    raw_name, f"fn_{obj.address:012x}"
                )
            if candidate in used_names:
                candidate = f"{candidate}_{obj.address:012x}"
            used_names.add(candidate)
            if candidate != raw_name:
                self.synthetic_function_names[obj.address] = candidate

    def _build_captured_scopes(self) -> None:
        # CreateClosure captures the context active at that exact bytecode offset.
        # Retaining this parent edge lets child bytecode recover lexical slot names
        # from the ScopeInfo that was pushed in its parent function.
        from .analysis.instruction import Instruction
        from .analysis.utils import parse_jump_target

        bytecodes = [
            obj for obj in self.objects if isinstance(obj, V8BytecodeArray)
        ]
        # Parent bytecode normally precedes its children, while the fixed-point
        # rounds also cover serializers that emit them in another order.
        for _round in range(max(1, len(bytecodes))):
            changed = False
            for bytecode in bytecodes:
                pool = self.bytecode_constant_pools.get(bytecode.address)
                if not pool or not pool.elements:
                    continue
                constants = {
                    index: value for index, value in enumerate(pool.elements)
                }
                instructions = [
                    Instruction.from_codeline(raw)
                    for raw in bytecode.instructions
                ]
                if not instructions:
                    continue
                offset_to_index = {
                    instr.offset: index for index, instr in enumerate(instructions)
                }
                inherited = tuple(
                    scope.address if isinstance(scope, V8ScopeInfo) else -1
                    for scope in reversed(
                        self.captured_scopes.get(bytecode.address, [])
                    )
                )
                # state = (outer-to-inner active scope addresses, accumulator
                # scope address, register->scope-address pairs)
                initial = (inherited, -1, tuple())
                pending = deque([(0, initial)])
                seen: Dict[int, set[tuple]] = {}

                while pending:
                    index, state = pending.popleft()
                    if index < 0 or index >= len(instructions):
                        continue
                    bucket = seen.setdefault(index, set())
                    if state in bucket:
                        continue
                    bucket.add(state)
                    # Malformed or highly irreducible inputs can expose many
                    # register aliases at a join. Context depth remains useful;
                    # cap equivalent offset states to keep analysis bounded.
                    if len(bucket) > 64:
                        continue

                    active, accumulator, register_items = state
                    registers = dict(register_items)
                    instr = instructions[index]
                    mnemonic = instr.mnemonic

                    if mnemonic in {"CreateFunctionContext", "CreateBlockContext"}:
                        scope = self._scope_from_constant_operand(
                            constants, instr.args[0] if instr.args else ""
                        )
                        accumulator = scope.address if scope else -1
                    elif mnemonic == "CreateCatchContext":
                        operand = instr.args[1] if len(instr.args) > 1 else ""
                        scope = self._scope_from_constant_operand(constants, operand)
                        accumulator = scope.address if scope else -1
                    elif (star := self._star_register(mnemonic, instr.args)):
                        if accumulator >= 0:
                            registers[star] = accumulator
                        else:
                            registers.pop(star, None)
                    elif mnemonic == "Ldar" and instr.args:
                        accumulator = registers.get(
                            self._normalized_register(instr.args[0]), -1
                        )
                    elif mnemonic == "Mov" and len(instr.args) >= 2:
                        source = self._normalized_register(instr.args[0])
                        target = self._normalized_register(instr.args[1])
                        if source in registers:
                            registers[target] = registers[source]
                        else:
                            registers.pop(target, None)
                    elif mnemonic == "PushContext":
                        if accumulator >= 0:
                            active = active + (accumulator,)
                    elif mnemonic == "PopContext":
                        active = active[:-1] if active else active
                        accumulator = -1
                    elif mnemonic == "CreateClosure" and instr.args:
                        child = self._function_from_constant_operand(
                            constants, instr.args[0]
                        )
                        if child and child.trusted_function_data:
                            child_bytecode = child.trusted_function_data.address
                            capture = [
                                self.get_object(address) if address >= 0 else None
                                for address in reversed(active)
                            ]
                            capture = [
                                scope if isinstance(scope, V8ScopeInfo) else None
                                for scope in capture
                            ]
                            if self.captured_scopes.get(child_bytecode) != capture:
                                self.captured_scopes[child_bytecode] = capture
                                changed = True
                            if self.capture_parents.get(child_bytecode) != bytecode.address:
                                self.capture_parents[child_bytecode] = bytecode.address
                                changed = True
                        accumulator = -1
                    else:
                        accumulator = -1

                    next_state = (
                        active,
                        accumulator,
                        tuple(sorted(registers.items())),
                    )
                    target = parse_jump_target(instr)
                    terminal = mnemonic in {
                        "Return", "Throw", "ReThrow", "Abort", "ThrowReferenceError"
                    }
                    unconditional = mnemonic in {
                        "Jump", "JumpConstant", "JumpLoop"
                    }
                    if target is not None and target in offset_to_index:
                        pending.append((offset_to_index[target], next_state))
                    if not terminal and not unconditional and index + 1 < len(instructions):
                        pending.append((index + 1, next_state))
            if not changed:
                break

    def _build_scope_slot_name_hints(self) -> None:
        """Collect exact slot/name pairs from V8's hole-check operands.

        Large function ScopeInfo objects can retain the local count while their
        serialized name array is omitted. Child bytecode still carries an exact
        ThrowReferenceErrorIfHole string beside the context load. Associate that
        evidence with the active captured ScopeInfo, but keep only unanimous
        names for each physical scope slot.
        """
        from .analysis.instruction import Instruction

        candidates: Dict[tuple[int, int], set[str]] = {}
        for bytecode in (
            obj for obj in self.objects if isinstance(obj, V8BytecodeArray)
        ):
            pool = self.bytecode_constant_pools.get(bytecode.address)
            if not pool or not pool.elements:
                continue
            constants = {index: value for index, value in enumerate(pool.elements)}
            instructions = [
                Instruction.from_codeline(raw) for raw in bytecode.instructions
            ]
            current_chain = list(self.captured_scopes.get(bytecode.address, []))
            accumulator_chain: Optional[List[Optional[V8ScopeInfo]]] = None
            register_chains: Dict[str, List[Optional[V8ScopeInfo]]] = {}
            accumulator_name: Optional[str] = None
            register_names: Dict[str, str] = {}

            for position, instr in enumerate(instructions):
                following = (
                    instructions[position + 1]
                    if position + 1 < len(instructions)
                    else None
                )
                if (
                    following
                    and following.mnemonic == "ThrowReferenceErrorIfHole"
                    and following.args
                ):
                    name = self._string_from_constant_operand(
                        constants, following.args[0]
                    )
                    resolved = self._scope_slot_for_instruction(
                        instr, current_chain, register_chains
                    )
                    if name and IDENT_RE.match(name) and resolved:
                        scope, index = resolved
                        candidates.setdefault((scope.address, index), set()).add(name)

                mnemonic = instr.mnemonic
                if mnemonic == "CreateClosure" and instr.args:
                    closure = self._function_from_constant_operand(
                        constants, instr.args[0]
                    )
                    accumulator_name = (
                        self.get_function_name(closure) if closure else None
                    )
                    accumulator_chain = None
                    continue
                if mnemonic in {
                    "StaCurrentContextSlot",
                    "StaCurrentScriptContextSlot",
                } and instr.args:
                    slot_match = re.match(r"^\[(\d+)\]$", instr.args[0])
                    if accumulator_name and slot_match and current_chain:
                        scope = current_chain[0]
                        index = int(slot_match.group(1)) - 2
                        if isinstance(scope, V8ScopeInfo) and index >= 0:
                            candidates.setdefault(
                                (scope.address, index), set()
                            ).add(accumulator_name)
                    accumulator_name = None
                    accumulator_chain = None
                    continue
                if mnemonic in {"CreateFunctionContext", "CreateBlockContext"}:
                    scope = self._scope_from_constant_operand(
                        constants, instr.args[0] if instr.args else ""
                    )
                    accumulator_chain = [scope] + current_chain if scope else None
                    accumulator_name = None
                    continue
                if mnemonic == "CreateCatchContext":
                    operand = instr.args[1] if len(instr.args) > 1 else ""
                    scope = self._scope_from_constant_operand(constants, operand)
                    accumulator_chain = [scope] + current_chain if scope else None
                    accumulator_name = None
                    continue
                star = self._star_register(mnemonic, instr.args)
                if star:
                    if accumulator_chain is not None:
                        register_chains[star] = list(accumulator_chain)
                    else:
                        register_chains.pop(star, None)
                    if accumulator_name:
                        register_names[star] = accumulator_name
                    else:
                        register_names.pop(star, None)
                    continue
                if mnemonic == "Ldar" and instr.args:
                    chain = register_chains.get(
                        self._normalized_register(instr.args[0])
                    )
                    accumulator_chain = list(chain) if chain else None
                    accumulator_name = register_names.get(
                        self._normalized_register(instr.args[0])
                    )
                    continue
                if mnemonic == "Mov" and len(instr.args) >= 2:
                    source = self._normalized_register(instr.args[0])
                    target = self._normalized_register(instr.args[1])
                    chain = register_chains.get(source)
                    if chain:
                        register_chains[target] = list(chain)
                    else:
                        register_chains.pop(target, None)
                    if source in register_names:
                        register_names[target] = register_names[source]
                    else:
                        register_names.pop(target, None)
                    continue
                if mnemonic == "PushContext":
                    destination = self._normalized_register(
                        instr.args[0] if instr.args else "context"
                    )
                    register_chains[destination] = list(current_chain)
                    if accumulator_chain is not None:
                        current_chain = list(accumulator_chain)
                    continue
                if mnemonic == "PopContext":
                    source = self._normalized_register(
                        instr.args[0] if instr.args else "context"
                    )
                    saved = register_chains.get(source)
                    current_chain = list(saved) if saved is not None else current_chain[1:]
                    accumulator_chain = None
                    continue
                accumulator_chain = None
                accumulator_name = None

        self.scope_slot_name_hints = {
            key: next(iter(names))
            for key, names in candidates.items()
            if len(names) == 1
        }

    def _string_from_constant_operand(
        self, constants: Dict[int, Any], operand: str
    ) -> Optional[str]:
        match = re.match(r"^\[(\d+)\]$", operand.strip())
        if not match:
            return None
        raw = constants.get(int(match.group(1)))
        if not isinstance(raw, V8Address):
            return None
        target = self.get_object(raw.address)
        return target.value if isinstance(target, V8String) else None

    def _scope_slot_for_instruction(
        self,
        instr,
        current_chain: List[Optional[V8ScopeInfo]],
        register_chains: Dict[str, List[Optional[V8ScopeInfo]]],
    ) -> Optional[tuple[V8ScopeInfo, int]]:
        current = {
            "LdaImmutableCurrentContextSlot",
            "LdaCurrentContextSlot",
            "LdaCurrentScriptContextSlot",
        }
        outer = {"LdaImmutableContextSlot", "LdaContextSlot"}
        if instr.mnemonic in current and instr.args:
            slot_match = re.match(r"^\[(\d+)\]$", instr.args[0])
            chain = current_chain
            depth = 0
        elif instr.mnemonic in outer and len(instr.args) >= 3:
            slot_match = re.match(r"^\[(\d+)\]$", instr.args[1])
            depth_match = re.match(r"^\[(\d+)\]$", instr.args[2])
            if not depth_match:
                return None
            depth = int(depth_match.group(1))
            register = self._normalized_register(instr.args[0])
            chain = register_chains.get(register, current_chain)
        else:
            return None
        if not slot_match or depth >= len(chain):
            return None
        scope = chain[depth]
        index = int(slot_match.group(1)) - 2
        if not isinstance(scope, V8ScopeInfo) or index < 0:
            return None
        return scope, index

    def _scope_from_constant_operand(
        self, constants: Dict[int, Any], operand: str
    ) -> Optional[V8ScopeInfo]:
        match = re.match(r"^\[(\d+)\]$", operand.strip())
        if not match:
            return None
        raw = constants.get(int(match.group(1)))
        if not isinstance(raw, V8Address):
            return None
        target = self.get_object(raw.address)
        return target if isinstance(target, V8ScopeInfo) else None

    def _function_from_constant_operand(
        self, constants: Dict[int, Any], operand: str
    ) -> Optional[V8SharedFunctionInfo]:
        match = re.match(r"^\[(\d+)\]$", operand.strip())
        if not match:
            return None
        raw = constants.get(int(match.group(1)))
        if not isinstance(raw, V8Address):
            return None
        target = self.get_object(raw.address)
        return target if isinstance(target, V8SharedFunctionInfo) else None

    @staticmethod
    def _normalized_register(token: str) -> str:
        token = token.strip()
        if token.startswith("a") and token[1:].isdigit():
            return f"arg{int(token[1:])}"
        return token.strip("<>")

    @classmethod
    def _star_register(cls, mnemonic: str, args: List[str]) -> Optional[str]:
        if mnemonic.startswith("Star") and mnemonic[4:].isdigit():
            return f"r{int(mnemonic[4:])}"
        if mnemonic == "Star" and args:
            return cls._normalized_register(args[0])
        return None

    def get_object(self, address: int) -> Optional[V8HeapObject]:
        return self.by_address.get(address)

    def get_function_for_bytecode(
        self, bytecode: V8BytecodeArray
    ) -> Optional[V8SharedFunctionInfo]:
        return self.bytecode_functions.get(bytecode.address)

    def get_function_name(self, sfi: V8SharedFunctionInfo) -> str:
        synthetic = self.synthetic_function_names.get(sfi.address)
        if synthetic:
            return synthetic
        raw_name: Optional[str] = None
        if sfi.name:
            ref = self.get_object(sfi.name.address)
            if isinstance(ref, V8String):
                raw_name = ref.value or None
            elif sfi.name.desc:
                raw_name = sfi.name.desc.strip("<>")
        if raw_name is None:
            raw_name = getattr(sfi, "raw_name", None)
        return js_binding_identifier(raw_name, f"fn_{sfi.address:012x}")

    def constant_pool_entries(self, bytecode: V8BytecodeArray) -> List[ConstantPoolEntry]:
        pool = self.bytecode_constant_pools.get(bytecode.address)
        if not pool or not pool.elements:
            return []

        entries: List[ConstantPoolEntry] = []
        for idx, raw in enumerate(pool.elements):
            entries.append(ConstantPoolEntry(idx, raw, self.format_value(raw)))
        return entries

    def format_value(self, raw: Any) -> str:
        if isinstance(raw, V8Smi):
            return str(raw.value)

        if isinstance(raw, float):
            if math.isnan(raw):
                return "NaN"
            if math.isinf(raw):
                return "Infinity" if raw > 0 else "-Infinity"
            return repr(raw)

        if isinstance(raw, V8Address):
            target = self.get_object(raw.address)
            if isinstance(target, V8String):
                return json.dumps(target.value)
            if isinstance(target, V8SharedFunctionInfo):
                return self.get_function_name(target)
            if isinstance(target, V8ArrayBoilerplateDescription):
                return self._format_array_boilerplate(target)
            if isinstance(target, V8ObjectBoilerplateDescription):
                return self._format_object_boilerplate(target)
            if isinstance(target, V8ClassBoilerplate):
                return self._format_class_boilerplate(target)
            if isinstance(target, V8FixedArray):
                return self._format_fixed_array(target)
            if isinstance(target, V8BytecodeArray):
                owner = self.bytecode_functions.get(target.address)
                if owner:
                    return f"<bytecode {self.get_function_name(owner)}>"
                return f"<Bytecode 0x{target.address:012x}>"
            if isinstance(target, V8ScopeInfo):
                return self._format_scope_info(target)
            desc_value = self._format_address_desc(raw.desc)
            if desc_value is not None:
                return desc_value
            if target:
                return json.dumps(f"<{target.i_type} 0x{target.address:012x}>")
            return json.dumps(raw.desc or f"0x{raw.address:012x}")

        if isinstance(raw, str):
            return json.dumps(raw)

        if raw is None:
            return "undefined"

        return str(raw)

    def _format_fixed_array(self, arr: V8FixedArray) -> str:
        parts = [self.format_value(el) for el in arr.elements]
        return "[" + ", ".join(parts) + "]"

    def _format_array_boilerplate(
        self, boilerplate: V8ArrayBoilerplateDescription
    ) -> str:
        if not boilerplate.constant_elements:
            return f"<ArrayBoilerplate {boilerplate.elements_kind}>"

        const = self.get_object(boilerplate.constant_elements.address)
        if isinstance(const, V8FixedArray):
            return self._format_fixed_array(const)
        if boilerplate.constant_elements.desc.strip("<>") in {
            "empty_fixed_array",
            # Legacy snapshots encode an empty double-array boilerplate by
            # pointing at the map object instead of a FixedArray instance.
            "fixed_double_array_map",
        }:
            return "[]"
        return f"<ArrayBoilerplate {boilerplate.elements_kind}>"

    def _format_address_desc(self, desc: str) -> Optional[str]:
        text = desc.strip()
        if not text:
            return None
        if text.startswith("<") and text.endswith(">"):
            inner = text[1:-1]
        else:
            inner = text
        if inner in {"true", "false", "null", "undefined"}:
            return inner
        if inner in {"uninitialized_value", "the_hole_value"}:
            return "undefined"
        if inner == "empty_object_boilerplate_description":
            return "{}"
        well_known_symbols = {
            "async_dispose_symbol": "asyncDispose",
            "async_iterator_symbol": "asyncIterator",
            "dispose_symbol": "dispose",
            "has_instance_symbol": "hasInstance",
            "is_concat_spreadable_symbol": "isConcatSpreadable",
            "iterator_symbol": "iterator",
            "match_all_symbol": "matchAll",
            "match_symbol": "match",
            "replace_symbol": "replace",
            "search_symbol": "search",
            "species_symbol": "species",
            "split_symbol": "split",
            "to_primitive_symbol": "toPrimitive",
            "to_string_tag_symbol": "toStringTag",
            "unscopables_symbol": "unscopables",
        }
        symbol = well_known_symbols.get(inner)
        if symbol:
            return f"Symbol.{symbol}"
        return None

    def _format_object_key(self, raw: Any) -> str:
        key = self.format_value(raw)
        if key.startswith('"') and key.endswith('"'):
            try:
                plain = json.loads(key)
            except json.JSONDecodeError:
                return key
            if plain.isidentifier():
                return plain
        return key

    def _format_object_boilerplate(
        self, boilerplate: V8ObjectBoilerplateDescription
    ) -> str:
        parts: List[str] = []
        entries = boilerplate.entries
        for idx in range(0, len(entries), 2):
            key = entries[idx]
            value = entries[idx + 1] if idx + 1 < len(entries) else None
            parts.append(f"{self._format_object_key(key)}: {self.format_value(value)}")
        return "{ " + ", ".join(parts) + " }"

    def _format_class_boilerplate(self, boilerplate: V8ClassBoilerplate) -> str:
        properties = []
        for prop in boilerplate.properties:
            item = {
                "placement": prop.placement,
                "kind": prop.kind,
                "valueIndex": prop.value_index,
            }
            if prop.computed_key_index is not None:
                item["keyArgument"] = prop.computed_key_index
            else:
                item["keyExpression"] = self.format_value(prop.key)
            properties.append(item)
        return json.dumps(
            {
                "__v8ClassBoilerplate": True,
                "argumentsCount": boilerplate.arguments_count,
                "properties": properties,
            },
            separators=(",", ":"),
        )

    def _format_scope_info(self, scope: V8ScopeInfo) -> str:
        scope_type = scope.scope_type or "Scope"
        return f"<ScopeInfo {scope_type}>"

    def scope_context_name(self, raw: Any, index: int = 0) -> Optional[str]:
        if not isinstance(raw, V8Address):
            return None
        target = self.get_object(raw.address)
        if not isinstance(target, V8ScopeInfo):
            return None
        if index < 0 or index >= len(target.context_slots):
            return self.scope_slot_name_hints.get((target.address, index))
        name = self.format_value(target.context_slots[index])
        if name.startswith('"') and name.endswith('"'):
            try:
                return json.loads(name)
            except json.JSONDecodeError:
                return None
        slot = target.context_slots[index]
        if isinstance(slot, V8Address):
            match = re.search(r"#([^>]+)", slot.desc)
            if match:
                return match.group(1)
        return self.scope_slot_name_hints.get((target.address, index))

    def unique_context_slot_name(self, slot: int) -> Optional[str]:
        index = slot - 2
        if index < 0:
            return None
        names = {
            name
            for obj in self.objects
            if isinstance(obj, V8ScopeInfo) and index < len(obj.context_slots)
            for name in [self.scope_context_name(V8Address(obj.address), index)]
            if name and IDENT_RE.match(name)
        }
        names.update(
            name
            for (scope_address, hinted_index), name in self.scope_slot_name_hints.items()
            if hinted_index == index and IDENT_RE.match(name)
        )
        return next(iter(names)) if len(names) == 1 else None

    def captured_context_slot_name(
        self, bytecode: V8BytecodeArray, slot: int, depth: int = 0
    ) -> Optional[str]:
        index = slot - 2
        if index < 0 or depth < 0:
            return None

        current_address = bytecode.address
        remaining_depth = depth
        visited: set[int] = set()
        while current_address not in visited:
            visited.add(current_address)
            scopes = self.captured_scopes.get(current_address, [])
            for scope in scopes:
                if remaining_depth == 0:
                    if scope is None or index >= len(scope.context_slots):
                        return None
                    return self.scope_context_name(V8Address(scope.address), index)
                remaining_depth -= 1

            parent = self.capture_parents.get(current_address)
            if parent is None:
                break
            current_address = parent
        return None
