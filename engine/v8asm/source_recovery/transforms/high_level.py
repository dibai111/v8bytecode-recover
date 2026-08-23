"""High-level JavaScript structure recovery pipeline."""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .binary import (
    _compact_adjacent_binary_temp_registers,
    _compact_accu_binary_exprs,
    _compact_accu_compare_if,
    _compact_self_binary_assignments,
    _recover_accu_conditional_expr,
    _recover_accu_conditional_return_expr,
)
from .arrays import _compact_fixed_array_builders, _compact_spread_array_builders
from .calls import _rewrite_bound_method_calls
from .cleanup import (
    _collapse_accu_store_return,
    _collapse_accu_store,
    _collapse_accu_push_context,
    _convert_unused_accu_assign_to_expr,
    _drop_duplicate_expr_before_assignment,
    _drop_unused_pure_reg_assignments,
    _drop_unused_pure_accu_loads,
    _flatten_else_after_early_exit,
    _inline_simple_accu_loads_into_next_line,
    _name_async_reject_handler_exceptions,
    _recover_post_decrement_loops,
    _recover_nested_shared_return_guard,
    _simplify_accu_throw,
    _simplify_accu_return,
)
from .common import _extract_indent, _find_block_end
from .iteration import (
    _avoid_for_of_loop_var_source_collision,
    _recover_array_spread_appends_until_stable,
    _recover_array_destructuring_until_stable,
    _recover_for_of,
    _recover_for_in_until_stable,
    _strip_for_of_recovery_noise,
    _strip_for_of_state_initializers,
)
from .defaults import recover_undefined_default_assignments
from .generators import _inline_generator_resume_mode_switches
from .guards import (
    _strip_iterator_exception_guard,
    _strip_pending_message_status_guard,
)
from .inline import _inline_single_use_registers
from .logical import (
    combine_nested_truthy_ifs,
    drop_noop_goto_empty_local_guards,
    drop_redundant_empty_else_truthy_guards,
    inline_accu_equality_condition_loads,
    inline_accu_condition_loads,
    recover_nullish_assignments,
    recover_or_fallback_assignments,
    recover_or_fallback_returns,
    rewrite_accu_condition_after_duplicate_store,
    rewrite_accu_condition_after_reg_store,
)
from .optional import recover_optional_chains
from .properties import (
    _compact_accu_property_stores,
    _compact_keyed_property_reads,
)
from .strings import _compact_string_concat_chains
from .switch import (
    _recover_constant_dispatch_assignments,
    _recover_switch_assignments,
    _recover_two_case_switch,
)




def recover_for_of_early_return(lines: List[str]) -> List[str]:
    """Recover `return <expr>` from V8's for-of resume-mode dispatch.

    A return inside for-of becomes: set flag=1, stash the value, join the
    iterator-close epilogue, then switch(flag) { case 0: throw value;
    case 1: return value }. Rewrite the arm that feeds the dispatch into a
    real return and neutralize the flag plumbing before later cleanup passes
    mistake its stores for dead scaffolding.
    """
    out = lines[:]
    i = 0
    while i < len(out):
        m_if = re.match(
            r"^(\s*)if \((truthy\(ACCU\)|!\(truthy\(ACCU\)\))\) \{$", out[i]
        )
        if not m_if:
            i += 1
            continue
        end = _find_block_end(out, i)
        if end is None:
            i += 1
            continue
        then_body = out[i + 1 : end]

        # Scan the arm for a flag register assigned 1 (possibly via ACCU) and
        # exactly one other register holding the returned expression.
        pending_literal: Optional[int] = None
        regs_literal: Dict[str, int] = {}
        regs_expr: List[Tuple[str, str]] = []
        for raw in then_body:
            s = raw.strip()
            accu_lit = re.match(r"^ACCU = (-?\d+)$", s)
            if accu_lit:
                pending_literal = int(accu_lit.group(1))
                continue
            reg_accu = re.match(r"^(r\d+) = ACCU$", s)
            if reg_accu:
                if pending_literal is not None:
                    regs_literal[reg_accu.group(1)] = pending_literal
                    pending_literal = None
                continue
            reg_lit = re.match(r"^(r\d+) = (-?\d+)$", s)
            if reg_lit:
                regs_literal[reg_lit.group(1)] = int(reg_lit.group(2))
                continue
            reg_expr = re.match(r"^(r\d+) = (.+)$", s)
            if reg_expr:
                regs_expr.append((reg_expr.group(1), reg_expr.group(2).strip()))

        flags = [r for r, v in regs_literal.items() if v == 1]
        if len(flags) != 1:
            i += 1
            continue
        flag_reg = flags[0]
        values = [(reg, expr) for reg, expr in regs_expr if reg != flag_reg]
        if len(values) != 1 or "ACCU" in values[0][1]:
            i += 1
            continue
        value_reg, value_expr = values[0]

        # Find the dispatch on this flag after the enclosing block closes.
        depth = 1
        j = i + 1
        while j < len(out) and depth > 0:
            depth += out[j].count("{") - out[j].count("}")
            j += 1
        window = "\n".join(out[j : min(len(out), j + 60)])
        dispatch = re.search(
            rf"switch \({re.escape(flag_reg)}\) \{{\n"
            rf"\s*case 0:\n\s*ACCU = (?P<v0>r\d+)\n\s*throw ACCU\n"
            rf"\s*case 1:\n\s*ACCU = (?P<v1>r\d+)\n\s*return ACCU",
            window,
        )
        import sys as _s; print('CAND', i, 'regs_expr=', regs_expr, 'flags=', flags, file=_s.stderr); print('DISPATCH CHECK', flag_reg, repr(value_reg), bool(dispatch), file=_s.stderr)
        if not dispatch:
            i += 1
            continue
        if dispatch.group("v0") != value_reg or dispatch.group("v1") != value_reg:
            i += 1
            continue

        indent = m_if.group(1)
        replacement = [
            f"{indent}if ({m_if.group(2)}) {{",
            f"{indent}  return {value_expr}",
            f"{indent}}}",
        ]
        out[i : end + 1] = replacement

        # Blank out the now-meaningless dispatch case bodies.
        base = i + len(replacement)
        k = base
        while k + 2 < min(len(out), base + 60):
            s = out[k].strip()
            inner0 = out[k + 1].strip()
            inner1 = out[k + 2].strip()
            reg = re.match(r"ACCU = (r\d+)$", inner0)
            if s.startswith("case ") and reg and (
                inner1.startswith("throw") or inner1.startswith("return")
            ):
                indent_text = out[k][: len(out[k]) - len(s)]
                out[k] = f"{indent_text}// resume-mode case removed"
                out[k + 1] = ""
                out[k + 2] = ""
                k += 3
                continue
            k += 1
        i += len(replacement)
    return out


def recover_js_structures(lines: List[str]) -> List[str]:
    current = recover_for_of_early_return(lines)
    current = _recover_array_spread_appends_until_stable(current)
    current = _recover_post_decrement_loops(current)
    current = _recover_for_of_until_stable(current)
    current = _recover_array_destructuring_until_stable(current)
    current = _strip_iterator_exception_guard(current)
    current = _strip_pending_message_status_guard(current)
    current = _compact_spread_array_builders(current)
    current = _compact_keyed_property_reads(current)
    current = _compact_accu_property_stores(current)
    current = recover_optional_chains(current)
    current = recover_nullish_assignments(current)
    current = recover_undefined_default_assignments(current)
    current = recover_or_fallback_assignments(current)
    current = rewrite_accu_condition_after_duplicate_store(current)
    current = rewrite_accu_condition_after_reg_store(current)
    current = inline_accu_equality_condition_loads(current)
    current = inline_accu_condition_loads(current)
    current = recover_or_fallback_returns(current)
    current = combine_nested_truthy_ifs(current)
    current = _recover_switch_assignments(current)
    current = _inline_single_use_registers(current)
    current = _recover_accu_conditional_expr(current)
    current = _compact_string_concat_chains(current)
    current = _simplify_accu_return(current)
    current = _recover_accu_conditional_return_expr(current)
    current = _simplify_accu_throw(current)
    current = _recover_nested_shared_return_guard(current)
    current = _flatten_else_after_early_exit(current)
    current = _recover_two_case_switch(current)
    current = _recover_constant_dispatch_assignments(current)
    current = _compact_accu_compare_if(current)
    current = _convert_unused_accu_assign_to_expr(current)
    current = inline_accu_equality_condition_loads(current)
    current = _rewrite_bound_method_calls(current)
    current = _compact_string_concat_chains(current)
    current = _drop_duplicate_expr_before_assignment(current)
    current = _collapse_accu_store(current)
    current = _collapse_accu_push_context(current)
    current = _collapse_accu_store_return(current)
    current = _compact_accu_binary_exprs(current)
    current = _compact_fixed_array_builders(current)
    current = _compact_adjacent_binary_temp_registers(current)
    current = _compact_self_binary_assignments(current)
    current = _recover_post_decrement_loops(current)
    current = _rewrite_bound_method_calls(current)
    current = _recover_array_destructuring_until_stable(current)
    current = _inline_generator_resume_mode_switches(current)
    current = _strip_for_of_state_initializers(current)
    current = _strip_for_of_recovery_noise(current)
    current = _avoid_for_of_loop_var_source_collision(current)
    current = _drop_unused_pure_accu_loads(current)
    current = _name_async_reject_handler_exceptions(current)
    current = _inline_simple_accu_loads_into_next_line(current)
    current = _drop_unused_pure_accu_loads(current)
    current = _drop_unused_pure_reg_assignments(current)
    current = recover_or_fallback_returns(current)
    current = combine_nested_truthy_ifs(current)
    current = _recover_nested_shared_return_guard(current)
    current = drop_redundant_empty_else_truthy_guards(current)
    current = drop_noop_goto_empty_local_guards(current)
    current = _recover_array_destructuring_until_stable(current)
    # Earlier propagation and cleanup can expose iterator append skeletons that
    # were not yet canonical at the start of the pipeline.
    current = _recover_array_spread_appends_until_stable(current)
    current = _recover_for_of_until_stable(current)
    current = _recover_for_in_until_stable(current)
    current = _strip_for_of_state_initializers(current)
    current = _strip_for_of_recovery_noise(current)
    current = _avoid_for_of_loop_var_source_collision(current)
    current = _normalize_block_indentation(current)
    return current


def _recover_for_of_until_stable(lines: List[str]) -> List[str]:
    current = lines
    while True:
        nxt = _recover_for_of(current)
        if nxt == current:
            return current
        current = nxt


def _normalize_block_indentation(lines: List[str]) -> List[str]:
    nonempty = [line for line in lines if line.strip()]
    if not nonempty:
        return lines
    base = min(len(_extract_indent(line)) for line in nonempty)
    base_indent = " " * base
    depth = 0
    switch_stack = []
    out: List[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue

        if stripped.startswith("}"):
            depth = max(0, depth - 1)
            while switch_stack and depth < switch_stack[-1]["label_depth"]:
                switch_stack.pop()

        is_case = bool(re.match(r"(?:case\b.+|default)\s*:", stripped))
        current_switch = (
            switch_stack[-1]
            if switch_stack and switch_stack[-1]["label_depth"] == depth
            else None
        )
        if is_case and current_switch is not None:
            current_switch["active"] = True

        active_cases = sum(1 for item in switch_stack if item["active"])
        if is_case and current_switch is not None:
            active_cases -= 1

        out.append(f"{base_indent}{'  ' * (depth + active_cases)}{stripped}")

        if stripped.endswith("{"):
            depth += 1
            if re.match(r"switch\s*\(.+\)\s*\{$", stripped):
                switch_stack.append({"label_depth": depth, "active": False})

    return out
