"""High-level V8 bytecode to JavaScript recovery pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import List, Optional

from .model import V8Address, V8ScopeInfo
from .model.bytecode import V8BytecodeArray

from .context import DecompilerContext, JS_RESERVED_IDENTIFIERS, js_binding_identifier
from .analysis.instruction import Instruction
from .parsing import parse_objects
from .transforms.pipeline import simplify_lines
from .transforms.file_cleanup import postprocess_level4_file
from .analysis.control_flow import decompile_to_statements
from .analysis.translator import InstructionTranslator
from .analysis.utils import parse_jump_target

IDENT_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
INDENT = "  "


def _format_params(bytecode: V8BytecodeArray) -> str:
    count = bytecode.parameter_count or 0
    user_params = max(0, count - 1)
    return ", ".join(f"arg{i}" for i in range(user_params))


def _format_constant_pool(ctx: DecompilerContext, bytecode: V8BytecodeArray) -> List[str]:
    entries = ctx.constant_pool_entries(bytecode)
    if not entries:
        return []
    lines = ["  // Constant pool:"]
    for entry in entries:
        lines.append(f"  //   [{entry.index}] = {entry.display}")
    return lines


def _sanitize_identifier(name: str, fallback: str) -> str:
    if IDENT_RE.match(name) and name not in JS_RESERVED_IDENTIFIERS:
        return name
    cleaned = re.sub(r"[^A-Za-z0-9_$]", "_", name.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned or cleaned in JS_RESERVED_IDENTIFIERS:
        return fallback
    if not IDENT_RE.match(cleaned):
        if cleaned[0].isdigit():
            cleaned = f"fn_{cleaned}"
        if not IDENT_RE.match(cleaned) or cleaned in JS_RESERVED_IDENTIFIERS:
            return fallback
    return cleaned


def _scope_binding_name(name: Optional[str], fallback: str) -> str:
    return js_binding_identifier(name, fallback)


def _format_register_locals(
    bytecode: V8BytecodeArray, *, derived_receiver: bool = False
) -> List[str]:
    lines = ["  let ACCU = undefined;"]
    if derived_receiver:
        lines.append("  let derived_this;")
    reg_count = bytecode.register_count or 0
    if reg_count <= 0:
        return lines
    regs = ", ".join(f"r{i}" for i in range(reg_count))
    lines.append(f"  let {regs};")
    return lines


def _function_name_for_bytecode(
    ctx: DecompilerContext, bytecode: V8BytecodeArray
) -> str:
    owner = ctx.get_function_for_bytecode(bytecode)
    if owner:
        raw_name = ctx.get_function_name(owner)
        fallback = f"fn_{bytecode.address:012x}"
        return _sanitize_identifier(raw_name, fallback)
    return f"bytecode_{bytecode.address:012x}"


def _function_context_names(
    ctx: DecompilerContext,
    translator: InstructionTranslator,
    instructions: List[Instruction],
) -> List[str]:
    names: List[str] = []
    seen: set[str] = set()
    for instr in instructions:
        if instr.mnemonic != "CreateFunctionContext" or not instr.args:
            continue
        token = instr.args[0].strip()
        if not (token.startswith("[") and token.endswith("]")):
            continue
        try:
            index = int(token[1:-1])
        except ValueError:
            continue
        entry = translator.constants.get(index)
        if not entry or not isinstance(entry.raw, V8Address):
            continue
        scope = ctx.get_object(entry.raw.address)
        if not isinstance(scope, V8ScopeInfo):
            continue
        for slot_index in range(len(scope.context_slots)):
            name = ctx.scope_context_name(entry.raw, slot_index)
            binding = _scope_binding_name(
                name, f"scope_{scope.address:012x}_{slot_index}"
            )
            if binding not in seen:
                seen.add(binding)
                names.append(binding)
    return names


def _runtime_prelude() -> str:
    return """const HOLE = Symbol("hole");
const __v8ctx = { slots: [] };
const context_slot = __v8ctx.slots;
const script_context = __v8ctx.slots;
let context = null;
let closure = undefined;

function truthy(v) { return !!v; }
function isNullish(v) { return v === null || v === undefined; }
function isJSReceiver(v) {
  const t = typeof v;
  return (t === "object" && v !== null) || t === "function";
}
function create_array_literal(v) { return Array.isArray(v) ? v.slice() : []; }
function create_object_literal(v) { return v && typeof v === "object" ? { ...v } : {}; }
function create_closure(fn) { return typeof fn === "function" ? fn : function () { return undefined; }; }
function create_function_context(scope, slots) { return { scope, slots: new Array(Number(slots) || 0) }; }
function create_block_context(scope) { return { scope, slots: [] }; }
function create_catch_context(value, scope) { return { value, scope, slots: [value] }; }
function pushContext(v) {
  const prev = context;
  if (v && typeof v === "object") v.outer = prev;
  context = v;
  return prev;
}
function resolveContext(v, depth) {
  let current = v;
  for (let i = 0; i < Number(depth || 0); i += 1) current = current && current.outer;
  return current;
}
function get_context_slot(v, slot, depth) {
  const target = resolveContext(v, depth);
  return target && target.slots ? target.slots[slot] : undefined;
}
function set_context_slot(v, slot, depth, value) {
  const target = resolveContext(v, depth);
  if (target && target.slots) target.slots[slot] = value;
  return value;
}
function GetIterator(v) { return v[Symbol.iterator](); }
function DeclareGlobals() { return undefined; }
function ensureDefined(name) {
  if (!(name in globalThis) && context_slot[name] === HOLE) {
    throw new ReferenceError(String(name));
  }
}
function ThrowIteratorResultNotAnObject(v) {
  throw new TypeError("Iterator result is not an object: " + String(v));
}
function _CopyDataPropertiesWithExcludedPropertiesOnStack(source, ...keys) {
  const out = {};
  for (const key of Object.keys(Object(source))) {
    if (!keys.includes(key)) out[key] = source[key];
  }
  return out;
}
"""


def render_level1(translator: InstructionTranslator, instructions: List[Instruction]) -> List[str]:
    lines: List[str] = []
    for instr in instructions:
        translated = translator.translate(instr)
        offset = instr.offset if instr.offset >= 0 else -1
        lines.append(f"  [{offset:4d}] {translated}")
    return lines


def render_level2(
    translator: InstructionTranslator,
    instructions: List[Instruction],
    recovered_regions: Optional[dict[int, List[str]]] = None,
) -> List[str]:
    statements = decompile_to_statements(translator, instructions, recovered_regions)
    lines: List[str] = []
    for stmt in statements:
        lines.extend(stmt.render(1))
    return lines


def render_level3(translator: InstructionTranslator, instructions: List[Instruction]) -> List[str]:
    return simplify_lines(render_level2(translator, instructions), recover_structures=False)


def _render_level4_fragment(
    translator: InstructionTranslator,
    instructions: List[Instruction],
    recovered_regions: Optional[dict[int, List[str]]] = None,
) -> List[str]:
    return simplify_lines(
        render_level2(translator, instructions, recovered_regions),
        recover_structures=True,
    )


def _indent_lines(lines: List[str]) -> List[str]:
    return [f"{INDENT}{line}" if line else line for line in lines]


def _instruction_index_at_or_after(
    instructions: List[Instruction], offset: int
) -> Optional[int]:
    for idx, instr in enumerate(instructions):
        if instr.offset >= offset:
            return idx
    return None


def _accu_load_expr(translator: InstructionTranslator, instr: Instruction) -> Optional[str]:
    text = translator.translate(instr).strip()
    match = re.match(r"^ACCU\s*=\s*(.+)$", text)
    if not match:
        return None
    expr = match.group(1).strip()
    if "ACCU" in expr:
        return None
    return expr


def _branch_path_condition(
    translator: InstructionTranslator,
    branch: Instruction,
    *,
    branch_taken: bool,
) -> Optional[str]:
    info = translator.branch_condition(branch)
    if not info:
        return None
    expr, branch_on_true = info
    if branch_taken == branch_on_true:
        return expr
    return f"!({expr})"


def _condition_with_load(condition: str, expr: str) -> Optional[str]:
    if not condition or not expr or "ACCU" in expr:
        return None
    return re.sub(r"\bACCU\b", lambda _match: expr, condition)


def _match_short_circuit_try_alternate(
    translator: InstructionTranslator,
    instructions: List[Instruction],
    prefix_instrs: List[Instruction],
    guarded_entry_offset: int,
    suffix_start_idx: Optional[int],
) -> Optional[tuple[List[Instruction], str, List[Instruction], List[Instruction]]]:
    if suffix_start_idx is None or suffix_start_idx >= len(instructions):
        return None
    if len(prefix_instrs) < 4:
        return None

    first_load, first_jump, second_load, second_jump = prefix_instrs[-4:]
    if parse_jump_target(first_jump) != guarded_entry_offset:
        return None
    alternate_offset = parse_jump_target(second_jump)
    if alternate_offset is None:
        return None

    after_try_jump = instructions[suffix_start_idx]
    if after_try_jump.mnemonic not in {"Jump", "JumpConstant"}:
        return None
    final_offset = parse_jump_target(after_try_jump)
    if final_offset is None:
        return None

    alternate_start_idx = _instruction_index_at_or_after(instructions, alternate_offset)
    final_suffix_idx = _instruction_index_at_or_after(instructions, final_offset)
    if (
        alternate_start_idx is None
        or final_suffix_idx is None
        or not (suffix_start_idx < alternate_start_idx < final_suffix_idx)
    ):
        return None

    first_expr = _accu_load_expr(translator, first_load)
    second_expr = _accu_load_expr(translator, second_load)
    first_condition = _branch_path_condition(
        translator, first_jump, branch_taken=True
    )
    second_condition = _branch_path_condition(
        translator, second_jump, branch_taken=False
    )
    if (
        first_expr is None
        or second_expr is None
        or first_condition is None
        or second_condition is None
    ):
        return None

    first_condition = _condition_with_load(first_condition, first_expr)
    second_condition = _condition_with_load(second_condition, second_expr)
    if first_condition is None or second_condition is None:
        return None

    setup_instrs = prefix_instrs[:-4]
    alternate_instrs = instructions[alternate_start_idx:final_suffix_idx]
    final_suffix_instrs = instructions[final_suffix_idx:]
    return (
        setup_instrs,
        f"{first_condition} || {second_condition}",
        alternate_instrs,
        final_suffix_instrs,
    )


def _catch_binding_name(
    ctx: DecompilerContext,
    translator: InstructionTranslator,
    instr: Instruction,
) -> str:
    if len(instr.args) < 2:
        return "e"
    token = instr.args[1].strip()
    if not (token.startswith("[") and token.endswith("]")):
        return "e"
    try:
        idx = int(token[1:-1])
    except ValueError:
        return "e"
    entry = translator.constants.get(idx)
    if not entry or not isinstance(entry.raw, V8Address):
        return "e"
    name = ctx.scope_context_name(entry.raw)
    if name and IDENT_RE.match(name) and name not in JS_RESERVED_IDENTIFIERS:
        return name
    if name:
        match = re.search(r"#([^>]+)", name)
        if (
            match
            and IDENT_RE.match(match.group(1))
            and match.group(1) not in JS_RESERVED_IDENTIFIERS
        ):
            return match.group(1)
    return "error"


def _catch_context_slot_names(
    ctx: DecompilerContext,
    translator: InstructionTranslator,
    instr: Instruction,
) -> dict[tuple[int, int], str]:
    if len(instr.args) < 2:
        return {}
    token = instr.args[1].strip()
    if not (token.startswith("[") and token.endswith("]")):
        return {}
    try:
        idx = int(token[1:-1])
    except ValueError:
        return {}
    entry = translator.constants.get(idx)
    if not entry or not isinstance(entry.raw, V8Address):
        return {}
    scope = ctx.get_object(entry.raw.address)
    if not isinstance(scope, V8ScopeInfo):
        return {}

    names: dict[tuple[int, int], str] = {}
    for index in range(len(scope.context_slots)):
        name = ctx.scope_context_name(entry.raw, index)
        fallback = "error" if index == 0 else f"catchValue{index}"
        names[(index + 2, 0)] = _scope_binding_name(name, fallback)
    return names


def _render_single_try_catch(
    ctx: DecompilerContext,
    translator: InstructionTranslator,
    instructions: List[Instruction],
    entry,
    remaining_entries=None,
) -> Optional[List[str]]:
    if remaining_entries is None:
        remaining_entries = []
    try_start_idx = _instruction_index_at_or_after(instructions, entry.start)
    try_end_idx = _instruction_index_at_or_after(instructions, entry.end)
    handler_idx = _instruction_index_at_or_after(instructions, entry.handler)
    if try_start_idx is None or try_end_idx is None or handler_idx is None:
        return None

    prefix_end_idx = try_start_idx
    if try_start_idx > 0:
        scaffold = instructions[try_start_idx - 1]
        if (
            scaffold.mnemonic == "Mov"
            and scaffold.args
            and scaffold.args[0] == "<context>"
        ):
            prefix_end_idx = try_start_idx - 1

    skip_jump_idx = (
        try_end_idx
        if try_end_idx < len(instructions)
        and instructions[try_end_idx].mnemonic in {"Jump", "JumpConstant"}
        else None
    )
    resume_offset = (
        parse_jump_target(instructions[skip_jump_idx]) if skip_jump_idx is not None else None
    )
    if resume_offset is None:
        for index in range(handler_idx, len(instructions) - 1):
            if instructions[index].mnemonic != "PopContext":
                continue
            following = instructions[index + 1]
            if following.mnemonic in {"Jump", "JumpConstant"}:
                resume_offset = parse_jump_target(following)
            elif following.offset >= 0:
                resume_offset = following.offset
            if resume_offset is not None:
                break
    suffix_start_idx = (
        _instruction_index_at_or_after(instructions, resume_offset)
        if resume_offset is not None
        else len(instructions)
    )

    try_instrs = instructions[try_start_idx:try_end_idx]
    if not try_instrs:
        return None
    if skip_jump_idx is None and try_instrs[-1].mnemonic != "Return":
        return None
    catch_instrs = instructions[
        handler_idx : suffix_start_idx if suffix_start_idx is not None else len(instructions)
    ]
    if not try_instrs or not catch_instrs:
        return None

    push_idx = next(
        (idx for idx, instr in enumerate(catch_instrs) if instr.mnemonic == "PushContext"),
        None,
    )
    if push_idx is None:
        return None
    pop_idx = next(
        (
            idx
            for idx, instr in enumerate(catch_instrs[push_idx + 1 :], start=push_idx + 1)
            if instr.mnemonic == "PopContext"
        ),
        None,
    )
    catch_body_instrs = catch_instrs[push_idx + 1 : pop_idx if pop_idx is not None else len(catch_instrs)]

    prefix_instrs = instructions[:prefix_end_idx]
    suffix_instrs = instructions[suffix_start_idx:] if suffix_start_idx is not None else []
    guard_condition: Optional[str] = None
    prefix_lines: Optional[List[str]] = None
    if prefix_instrs and resume_offset is not None:
        conditions: List[str] = []
        while prefix_instrs:
            guard = prefix_instrs[-1]
            guarded_prefix_offsets = {instr.offset for instr in prefix_instrs[:-1]}
            guard_is_branch_target = any(
                parse_jump_target(instr) == guard.offset for instr in prefix_instrs[:-1]
            )
            prefix_has_other_external_jump = any(
                (target := parse_jump_target(instr)) is not None
                and target not in guarded_prefix_offsets
                and target != resume_offset
                for instr in prefix_instrs[:-1]
            )
            if (
                guard_is_branch_target
                or prefix_has_other_external_jump
                or not guard.mnemonic.startswith("JumpIf")
                or parse_jump_target(guard) != resume_offset
            ):
                break

            condition = translator.fallthrough_condition(guard)
            if not condition:
                break
            prefix_instrs = prefix_instrs[:-1]
            if "ACCU" in condition and prefix_instrs:
                last_prefix_text = translator.translate(prefix_instrs[-1]).strip()
                last_accu = re.match(r"^ACCU\s*=\s*(.+)$", last_prefix_text)
                if last_accu and "ACCU" not in last_accu.group(1):
                    prefix_instrs = prefix_instrs[:-1]
                    condition = re.sub(
                        r"\bACCU\b", lambda _match: last_accu.group(1).strip(), condition
                    )
            conditions.insert(0, condition)

        if conditions:
            guard_condition = (
                conditions[0]
                if len(conditions) == 1
                else " && ".join(f"({condition})" for condition in conditions)
            )

    catch_ctx_instr = next(
        (instr for instr in catch_instrs[:push_idx] if instr.mnemonic == "CreateCatchContext"),
        None,
    )
    if catch_ctx_instr is None:
        return None
    catch_name = (
        _catch_binding_name(ctx, translator, catch_ctx_instr)
    )

    try_lines = _render_handler_fragment(
        ctx, translator, try_instrs, remaining_entries
    )
    escape_label = f"handlerRegion{entry.start}"
    try_lines, has_region_escape = _rewrite_handler_region_escapes(
        try_lines, resume_offset, escape_label
    )
    original_slot_names = translator.context_slot_names.copy()
    catch_slot_names = _catch_context_slot_names(ctx, translator, catch_ctx_instr)
    try:
        translator.context_slot_names = {
            key: value
            for key, value in original_slot_names.items()
            if key[1] != 0
        }
        translator.context_slot_names.update(catch_slot_names)
        catch_lines = _render_handler_fragment(
            ctx, translator, catch_body_instrs, remaining_entries
        )
    finally:
        translator.context_slot_names = original_slot_names
    if not try_lines:
        return None

    try_catch_lines: List[str] = []
    try_catch_lines.append(f"{INDENT}try {{")
    try_catch_lines.extend(_indent_lines(try_lines))
    try_catch_lines.append(f"{INDENT}}} catch ({catch_name}) {{")
    try_catch_lines.extend(_indent_lines(catch_lines))
    try_catch_lines.append(f"{INDENT}}}")
    if has_region_escape:
        try_catch_lines = [
            f"{INDENT}{escape_label}: {{",
            *_indent_lines(try_catch_lines),
            f"{INDENT}}}",
        ]
    out: List[str] = []
    guarded_entry_offset = instructions[prefix_end_idx].offset
    alternate = _match_short_circuit_try_alternate(
        translator,
        instructions,
        prefix_instrs,
        guarded_entry_offset,
        suffix_start_idx,
    )
    guarded_handler = (
        None
        if alternate is not None
        else _match_guard_across_handler_region(
            translator,
            instructions,
            prefix_instrs,
            resume_offset,
            suffix_start_idx,
        )
    )
    if guarded_handler is not None:
        (
            leading_instrs,
            condition,
            setup_instrs,
            guarded_suffix_instrs,
            final_suffix_instrs,
        ) = guarded_handler
        if leading_instrs:
            out.extend(
                _render_handler_fragment(
                    ctx, translator, leading_instrs, remaining_entries
                )
            )
        out.append(f"{INDENT}if ({condition}) {{")
        if setup_instrs:
            out.extend(
                _indent_lines(
                    _render_handler_fragment(
                        ctx, translator, setup_instrs, remaining_entries
                    )
                )
            )
        out.extend(_indent_lines(try_catch_lines))
        if guarded_suffix_instrs:
            out.extend(
                _indent_lines(
                    _render_handler_fragment(
                        ctx, translator, guarded_suffix_instrs, remaining_entries
                    )
                )
            )
        out.append(f"{INDENT}}}")
        if final_suffix_instrs:
            out.extend(
                _render_handler_fragment(
                    ctx, translator, final_suffix_instrs, remaining_entries
                )
            )
        return out

    if alternate is not None:
        setup_instrs, condition, alternate_instrs, final_suffix_instrs = alternate
        if setup_instrs:
            out.extend(
                _render_handler_fragment(
                    ctx, translator, setup_instrs, remaining_entries
                )
            )
        out.append(f"{INDENT}if ({condition}) {{")
        out.extend(_indent_lines(try_catch_lines))
        out.append(f"{INDENT}}} else {{")
        out.extend(
            _indent_lines(
                _render_handler_fragment(
                    ctx, translator, alternate_instrs, remaining_entries
                )
            )
        )
        out.append(f"{INDENT}}}")
        if final_suffix_instrs:
            out.extend(
                _render_handler_fragment(
                    ctx, translator, final_suffix_instrs, remaining_entries
                )
            )
        return out
    if prefix_lines is not None:
        out.extend(prefix_lines)
    elif prefix_instrs:
        out.extend(
            _render_handler_fragment(
                ctx, translator, prefix_instrs, remaining_entries
            )
        )
    if guard_condition:
        out.append(f"{INDENT}if ({guard_condition}) {{")
        out.extend(_indent_lines(try_catch_lines))
        out.append(f"{INDENT}}}")
    else:
        out.extend(try_catch_lines)
    if suffix_instrs:
        out.extend(
            _render_handler_fragment(
                ctx, translator, suffix_instrs, remaining_entries
            )
        )
    return out


def _match_guard_across_handler_region(
    translator: InstructionTranslator,
    instructions: List[Instruction],
    prefix_instrs: List[Instruction],
    resume_offset: Optional[int],
    suffix_start_idx: Optional[int],
) -> Optional[
    tuple[
        List[Instruction],
        str,
        List[Instruction],
        List[Instruction],
        List[Instruction],
    ]
]:
    if resume_offset is None or suffix_start_idx is None:
        return None
    for branch_idx in range(len(prefix_instrs) - 1, -1, -1):
        branch = prefix_instrs[branch_idx]
        target = parse_jump_target(branch)
        if (
            target is None
            or target <= resume_offset
            or not branch.mnemonic.startswith("JumpIf")
        ):
            continue
        final_suffix_idx = _instruction_index_at_or_after(instructions, target)
        if final_suffix_idx is None:
            fragment_offsets = [item.offset for item in instructions if item.offset >= 0]
            if not fragment_offsets or target <= max(fragment_offsets):
                continue
            final_suffix_idx = len(instructions)
        if final_suffix_idx <= suffix_start_idx:
            continue
        if any(
            parse_jump_target(item) is not None
            for item in prefix_instrs[branch_idx + 1 :]
        ):
            continue

        condition = translator.fallthrough_condition(branch)
        if not condition:
            continue
        leading_end = branch_idx
        if "ACCU" in condition and branch_idx > 0:
            load_text = translator.translate(prefix_instrs[branch_idx - 1]).strip()
            load = re.match(r"^ACCU\s*=\s*(.+)$", load_text)
            if load and "ACCU" not in load.group(1):
                condition = re.sub(
                    r"\bACCU\b", lambda _match: load.group(1).strip(), condition
                )
                leading_end -= 1

        return (
            prefix_instrs[:leading_end],
            condition,
            prefix_instrs[branch_idx + 1 :],
            instructions[suffix_start_idx:final_suffix_idx],
            instructions[final_suffix_idx:],
        )
    return None


def _rewrite_handler_region_escapes(
    lines: List[str], resume_offset: Optional[int], label: str
) -> tuple[List[str], bool]:
    if resume_offset is None:
        return lines, False
    target = str(resume_offset)
    out: List[str] = []
    changed = False
    for line in lines:
        conditional = re.match(
            rf"^(\s*)if \((.+)\) goto offset_{re.escape(target)}$", line
        )
        if conditional:
            out.extend(
                [
                    f"{conditional.group(1)}if ({conditional.group(2)}) {{",
                    f"{conditional.group(1)}  break {label}",
                    f"{conditional.group(1)}}}",
                ]
            )
            changed = True
            continue
        direct = re.match(
            rf"^(\s*)(?:// )?(?:loop )?goto offset_{re.escape(target)}$", line
        )
        if direct:
            out.append(f"{direct.group(1)}break {label}")
            changed = True
            continue
        out.append(line)
    return out, changed


def _entry_fits_fragment(instructions: List[Instruction], entry) -> bool:
    if not instructions:
        return False
    start_idx = _instruction_index_at_or_after(instructions, entry.start)
    end_idx = _instruction_index_at_or_after(instructions, entry.end)
    handler_idx = _instruction_index_at_or_after(instructions, entry.handler)
    if start_idx is None or end_idx is None or handler_idx is None:
        return False
    return start_idx <= end_idx <= handler_idx < len(instructions)


def _render_handler_fragment(
    ctx: DecompilerContext,
    translator: InstructionTranslator,
    instructions: List[Instruction],
    entries,
) -> List[str]:
    candidates = sorted(
        (entry for entry in entries if _entry_fits_fragment(instructions, entry)),
        key=lambda entry: (entry.end - entry.start, entry.end),
        reverse=True,
    )
    for entry in candidates:
        remaining = [candidate for candidate in entries if candidate is not entry]
        rendered = _render_single_try_catch(
            ctx,
            translator,
            instructions,
            entry,
            remaining_entries=remaining,
        )
        if rendered is not None:
            return rendered
    return _render_level4_fragment(translator, instructions)


def _handler_collapse_layout(instructions: List[Instruction], entry):
    try_start_idx = _instruction_index_at_or_after(instructions, entry.start)
    try_end_idx = _instruction_index_at_or_after(instructions, entry.end)
    handler_idx = _instruction_index_at_or_after(instructions, entry.handler)
    if try_start_idx is None or try_end_idx is None or handler_idx is None:
        return None
    if not (try_start_idx <= try_end_idx <= handler_idx < len(instructions)):
        return None

    skip_jump_idx = (
        try_end_idx
        if try_end_idx < len(instructions)
        and instructions[try_end_idx].mnemonic in {"Jump", "JumpConstant"}
        else None
    )
    resume_offset = (
        parse_jump_target(instructions[skip_jump_idx])
        if skip_jump_idx is not None
        else None
    )
    if resume_offset is None:
        for index in range(handler_idx, len(instructions) - 1):
            if instructions[index].mnemonic != "PopContext":
                continue
            following = instructions[index + 1]
            if following.mnemonic in {"Jump", "JumpConstant"}:
                resume_offset = parse_jump_target(following)
            elif following.offset >= 0:
                resume_offset = following.offset
            if resume_offset is not None:
                break
    if resume_offset is None:
        return None

    suffix_start_idx = _instruction_index_at_or_after(instructions, resume_offset)
    if suffix_start_idx is None or suffix_start_idx <= handler_idx:
        return None
    try_instrs = instructions[try_start_idx:try_end_idx]
    if not try_instrs:
        return None
    if skip_jump_idx is None and try_instrs[-1].mnemonic != "Return":
        return None

    catch_instrs = instructions[handler_idx:suffix_start_idx]
    push_idx = next(
        (idx for idx, instr in enumerate(catch_instrs) if instr.mnemonic == "PushContext"),
        None,
    )
    if push_idx is None:
        return None
    pop_idx = next(
        (
            idx
            for idx, instr in enumerate(catch_instrs[push_idx + 1 :], start=push_idx + 1)
            if instr.mnemonic == "PopContext"
        ),
        None,
    )
    catch_body_end = pop_idx if pop_idx is not None else len(catch_instrs)
    catch_body_instrs = catch_instrs[push_idx + 1 : catch_body_end]
    catch_ctx_instr = next(
        (instr for instr in catch_instrs[:push_idx] if instr.mnemonic == "CreateCatchContext"),
        None,
    )
    if catch_ctx_instr is None:
        return None
    return (
        try_start_idx,
        suffix_start_idx,
        try_instrs,
        catch_body_instrs,
        catch_ctx_instr,
    )


def _collapse_handler_regions(
    ctx: DecompilerContext,
    translator: InstructionTranslator,
    instructions: List[Instruction],
    entries,
) -> tuple[List[Instruction], dict[int, List[str]]]:
    layouts = []
    for entry in entries:
        layout = _handler_collapse_layout(instructions, entry)
        if layout is not None:
            layouts.append((entry, layout))
    if not layouts:
        return instructions, {}

    entry, layout = min(
        layouts,
        key=lambda item: (item[1][0], -(item[1][1] - item[1][0])),
    )
    (
        try_start_idx,
        suffix_start_idx,
        try_instrs,
        catch_body_instrs,
        catch_ctx_instr,
    ) = layout
    remaining_entries = [candidate for candidate in entries if candidate is not entry]

    prefix_instrs, prefix_regions = _collapse_handler_regions(
        ctx,
        translator,
        instructions[:try_start_idx],
        remaining_entries,
    )
    collapsed_try, try_regions = _collapse_handler_regions(
        ctx,
        translator,
        try_instrs,
        remaining_entries,
    )
    suffix_instrs, suffix_regions = _collapse_handler_regions(
        ctx,
        translator,
        instructions[suffix_start_idx:],
        remaining_entries,
    )

    try_lines = _render_level4_fragment(translator, collapsed_try, try_regions)
    original_slot_names = translator.context_slot_names.copy()
    catch_slot_names = _catch_context_slot_names(ctx, translator, catch_ctx_instr)
    try:
        translator.context_slot_names = {
            key: value
            for key, value in original_slot_names.items()
            if key[1] != 0
        }
        translator.context_slot_names.update(catch_slot_names)
        collapsed_catch, catch_regions = _collapse_handler_regions(
            ctx,
            translator,
            catch_body_instrs,
            remaining_entries,
        )
        catch_lines = _render_level4_fragment(
            translator, collapsed_catch, catch_regions
        )
    finally:
        translator.context_slot_names = original_slot_names

    catch_name = _catch_binding_name(ctx, translator, catch_ctx_instr)
    recovered_lines = ["try {"]
    recovered_lines.extend(try_lines)
    recovered_lines.append(f"}} catch ({catch_name}) {{")
    recovered_lines.extend(catch_lines)
    recovered_lines.append("}")

    marker_offset = instructions[try_start_idx].offset
    marker = Instruction(
        offset=marker_offset,
        mnemonic="RecoveredTryCatch",
        args=[],
        raw_line="",
    )
    recovered_regions = {
        **prefix_regions,
        marker_offset: recovered_lines,
        **suffix_regions,
    }
    return [*prefix_instrs, marker, *suffix_instrs], recovered_regions


def _render_simple_try_catch(
    ctx: DecompilerContext,
    bytecode: V8BytecodeArray,
    translator: InstructionTranslator,
    instructions: List[Instruction],
) -> Optional[List[str]]:
    if not bytecode.handler_entries:
        return None
    rendered = _render_handler_fragment(
        ctx, translator, instructions, bytecode.handler_entries
    )
    baseline = _render_level4_fragment(translator, instructions)
    return rendered if rendered != baseline else None


def render_level4(
    ctx: DecompilerContext,
    bytecode: V8BytecodeArray,
    translator: InstructionTranslator,
    instructions: List[Instruction],
) -> List[str]:
    if translator.is_async_generator:
        instructions = _prepare_generator_instructions(translator, instructions)
    legacy_recovered = _render_simple_try_catch(
        ctx, bytecode, translator, instructions
    )
    collapsed, recovered_regions = _collapse_handler_regions(
        ctx, translator, instructions, bytecode.handler_entries
    )
    if recovered_regions:
        collapsed_recovered = _render_level4_fragment(
            translator, collapsed, recovered_regions
        )
        collapsed_gotos = sum(
            1 for line in collapsed_recovered if "goto offset_" in line
        )
        legacy_gotos = (
            sum(1 for line in legacy_recovered if "goto offset_" in line)
            if legacy_recovered is not None
            else collapsed_gotos + 1
        )
        if collapsed_gotos < legacy_gotos:
            return collapsed_recovered
    if legacy_recovered is not None:
        return legacy_recovered
    return _render_level4_fragment(translator, instructions)


def _intrinsic_name(instr: Instruction) -> Optional[str]:
    if instr.mnemonic != "InvokeIntrinsic" or not instr.args:
        return None
    return instr.args[0].strip("[]")


def _switch_case_target(
    translator: InstructionTranslator,
    instr: Instruction,
    case_value: int,
) -> Optional[int]:
    if instr.mnemonic != "SwitchOnSmiNoFeedback" or len(instr.args) < 3:
        return None
    values: List[int] = []
    for token in instr.args[:3]:
        match = re.fullmatch(r"\[(-?\d+)\]", token.strip())
        if not match:
            return None
        values.append(int(match.group(1)))
    table_index, table_size, first_case = values
    position = case_value - first_case
    if position < 0 or position >= table_size:
        return None
    entry = translator.constants.get(table_index + position)
    if entry is None or not re.fullmatch(r"-?\d+", entry.display.strip()):
        return None
    return instr.offset + int(entry.display)


def _prepare_generator_instructions(
    translator: InstructionTranslator,
    instructions: List[Instruction],
) -> List[Instruction]:
    """Remove V8 generator resume dispatch while retaining source operations."""
    if not instructions:
        return instructions
    offset_to_index = {instr.offset: index for index, instr in enumerate(instructions)}
    removed: set[int] = set()

    for index, instr in enumerate(instructions):
        if instr.mnemonic == "SwitchOnGeneratorState":
            removed.add(index)
        if _intrinsic_name(instr) != "CreateJSGeneratorObject":
            continue
        removed.add(index)
        if index + 1 < len(instructions) and instructions[index + 1].mnemonic.startswith("Star"):
            removed.add(index + 1)
        for previous in range(max(0, index - 2), index):
            if instructions[previous].mnemonic == "Mov":
                removed.add(previous)

    for suspend_index, suspend in enumerate(instructions):
        if suspend.mnemonic != "SuspendGenerator":
            continue
        kind = (
            _intrinsic_name(instructions[suspend_index - 1])
            if suspend_index > 0
            else None
        )
        removed.add(suspend_index)
        resume_index = suspend_index + 1
        if (
            resume_index >= len(instructions)
            or instructions[resume_index].mnemonic != "ResumeGenerator"
        ):
            continue
        removed.add(resume_index)

        result_index = resume_index + 1
        if (
            kind is None
            and result_index < len(instructions)
            and instructions[result_index].mnemonic.startswith("Star")
        ):
            removed.add(result_index)

        mode_index = next(
            (
                index
                for index in range(result_index, min(len(instructions), result_index + 5))
                if _intrinsic_name(instructions[index]) == "GeneratorGetResumeMode"
            ),
            None,
        )
        if mode_index is None:
            continue

        normal_target: Optional[int] = None
        for probe in range(mode_index + 1, min(len(instructions), mode_index + 10)):
            current = instructions[probe]
            if current.mnemonic == "SwitchOnSmiNoFeedback":
                normal_target = _switch_case_target(translator, current, 0)
                break
            if current.mnemonic in {"JumpIfTrue", "JumpIfTrueConstant"}:
                normal_target = parse_jump_target(current)
                break
        target_index = offset_to_index.get(normal_target) if normal_target is not None else None
        if target_index is None or target_index <= mode_index:
            continue
        removed.update(range(mode_index, target_index))

    return [
        instr for index, instr in enumerate(instructions) if index not in removed
    ]


def decompile_bytecode(ctx: DecompilerContext, bytecode: V8BytecodeArray, level: int) -> str:
    fn_name = _function_name_for_bytecode(ctx, bytecode)
    metadata = (
        f"  // Bytecode 0x{bytecode.address:012x} "
        f"params={bytecode.parameter_count} "
        f"regs={bytecode.register_count} frame={bytecode.frame_size}"
    )

    translator = InstructionTranslator(ctx, bytecode)
    instructions = [Instruction.from_codeline(raw) for raw in bytecode.instructions]
    function_keyword = "async function*" if translator.is_async_generator else "function"
    header = f"{function_keyword} {fn_name}({_format_params(bytecode)}) {{"

    markers: List[str] = []
    parent_address = ctx.capture_parents.get(bytecode.address)
    parent = ctx.get_object(parent_address) if parent_address is not None else None
    if isinstance(parent, V8BytecodeArray):
        markers.append(
            f"  // V8 capture parent: {_function_name_for_bytecode(ctx, parent)}"
        )
    context_names = _function_context_names(ctx, translator, instructions)
    if context_names:
        markers.append(
            "  // V8 context locals: "
            + json.dumps(context_names, ensure_ascii=True)
        )

    note: List[str] = []
    try:
        if level == 1:
            body_lines = render_level1(translator, instructions)
        elif level == 2:
            body_lines = render_level2(translator, instructions)
        elif level == 3:
            body_lines = render_level3(translator, instructions)
        else:
            body_lines = render_level4(ctx, bytecode, translator, instructions)
    except RecursionError:
        note.append("  // WARNING: structurer recursion overflow, fallback to level-1 linear output")
        body_lines = [f"  // {line.strip()}" for line in render_level1(translator, instructions)]
    except Exception as exc:
        detail = str(exc).replace("\n", " ")
        note.append(
            f"  // WARNING: decompile error ({type(exc).__name__}: {detail}), "
            "fallback to level-1 linear output"
        )
        body_lines = [f"  // {line.strip()}" for line in render_level1(translator, instructions)]

    lines: List[str] = [header, metadata]
    lines.extend(
        _format_register_locals(
            bytecode, derived_receiver=translator.uses_derived_receiver
        )
    )
    lines.extend(markers)
    lines.extend(note)
    lines.extend(_format_constant_pool(ctx, bytecode))
    lines.extend(body_lines)
    lines.append("}")
    return "\n".join(lines)


def _read_disassembly_lines(path: Path) -> List[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def decompile_file(path: Path, level: int, runtime: bool = False) -> str:
    data = _read_disassembly_lines(path)
    objects = parse_objects(data)
    ctx = DecompilerContext(objects)

    outputs: List[str] = []
    if runtime:
        outputs.append(_runtime_prelude().rstrip())
    for obj in objects:
        if isinstance(obj, V8BytecodeArray):
            outputs.append(decompile_bytecode(ctx, obj, level))
    output = "\n\n".join(outputs)
    if level >= 4:
        output = postprocess_level4_file(output)
    return output


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    default_input = script_dir.parent / "samples" / "main.d8.jsc.txt"
    parser = argparse.ArgumentParser(description="V8 bytecode decompiler")
    parser.add_argument(
        "input",
        nargs="?",
        default=str(default_input),
        help="path to disassembled bytecode dump",
    )
    parser.add_argument(
        "--level",
        type=int,
        choices=(1, 2, 3, 4),
        default=3,
        help=(
            "Select decompilation level: "
            "1 = linear bytecode-aligned, "
            "2 = structured CFG, "
            "3 = structured + safe simplifications, "
            "4 = high-level JS-like recovery"
        ),
    )
    parser.add_argument(
        "--runtime",
        action="store_true",
        help="Emit a lightweight JS runtime prelude to make pseudo code easier to run",
    )
    args = parser.parse_args()
    path = Path(args.input)
    if not path.exists():
        raise SystemExit(f"{path} does not exist")
    print(decompile_file(path, args.level, runtime=args.runtime))


if __name__ == "__main__":
    main()
