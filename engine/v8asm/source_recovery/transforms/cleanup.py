"""Low-level scaffolding cleanup transforms."""

from __future__ import annotations

import re
from typing import Dict, List

from .common import _extract_indent, _find_block_end


def _is_pure_expr_level4(expr: str) -> bool:
    expr = expr.strip()
    if not expr:
        return False
    if expr.startswith(("true", "false", "null", "undefined", "HOLE", '"', "'")):
        return True
    if expr.startswith(("[", "{", "String(")):
        return True
    if re.fullmatch(r"[-+]?\d+", expr):
        return True
    if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*(\.[A-Za-z_$][A-Za-z0-9_$]*)*", expr):
        return True
    if re.fullmatch(
        r"[A-Za-z_$][A-Za-z0-9_$]*(?:\?\.(?:[A-Za-z_$][A-Za-z0-9_$]*|\[[^\]]+\]))+",
        expr,
    ):
        return True
    if expr.startswith(("context_slot[", "script_context[", "globalThis[")):
        if "(" in expr:
            return False
        return True
    if expr.startswith("(") and expr.endswith(")") and "call(" not in expr:
        return True
    return False


def _simplify_accu_return(lines: List[str]) -> List[str]:
    out: List[str] = []
    i = 0
    while i < len(lines):
        if i + 1 < len(lines):
            s0 = lines[i].strip()
            s1 = lines[i + 1].strip()
            if s1 == "return ACCU" and s0.startswith("ACCU = "):
                expr = s0[len("ACCU = ") :].strip()
                indent = _extract_indent(lines[i + 1])
                out.append(f"{indent}return {expr}")
                i += 2
                continue
        out.append(lines[i])
        i += 1
    return out


def _simplify_accu_throw(lines: List[str]) -> List[str]:
    out: List[str] = []
    i = 0
    while i < len(lines):
        if i + 1 < len(lines):
            s0 = lines[i].strip()
            s1 = lines[i + 1].strip()
            match = re.match(r"^ACCU\s*=\s*(.+)$", s0)
            if match and s1 == "throw ACCU":
                expr = match.group(1).strip()
                if "ACCU" not in expr:
                    indent = _extract_indent(lines[i + 1])
                    out.append(f"{indent}throw {expr}")
                    i += 2
                    continue
        out.append(lines[i])
        i += 1
    return out


def _recover_post_decrement_loops(lines: List[str]) -> List[str]:
    """Recover V8's ToNumber/Dec loop test without changing evaluation order."""
    out = lines[:]
    i = 0
    while i + 7 < len(out):
        outer = re.match(r"^(\s*)while \(!\(truthy\(ACCU\)\)\) \{$", out[i])
        if not outer:
            i += 1
            continue

        load = re.match(r"^\s*ACCU = (r\d+)$", out[i + 1])
        convert = out[i + 2].strip() == "ACCU = Number(ACCU)"
        save = re.match(r"^\s*(r\d+) = ACCU$", out[i + 3])
        decrement = out[i + 4].strip() == "ACCU = (ACCU - 1)"
        store = re.match(r"^\s*(r\d+) = ACCU$", out[i + 5])
        if not load or not convert or not save or not decrement or not store:
            i += 1
            continue
        counter = load.group(1)
        previous = save.group(1)
        if store.group(1) != counter:
            i += 1
            continue

        guard = re.match(
            rf"^(\s*)if \(!\({re.escape(previous)} === 0\)\) \{{$",
            out[i + 6],
        )
        if not guard:
            i += 1
            continue
        guard_end = _find_block_end(out, i + 6)
        outer_end = _find_block_end(out, i)
        if guard_end is None or outer_end is None or guard_end + 1 != outer_end:
            i += 1
            continue

        indent = outer.group(1)
        body = out[i + 7 : guard_end]
        dedent = len(guard.group(1)) - len(indent)
        recovered = [
            f"{indent}while (({previous} = Number({counter})) !== 0) {{",
            f"{indent}  {counter} = ({previous} - 1)",
        ]
        recovered.extend(
            line[dedent:] if line.strip() and len(line) >= dedent else line
            for line in body
        )
        recovered.append(f"{indent}}}")
        out[i : outer_end + 1] = recovered
        i += len(recovered)
    return out


def _flatten_else_after_early_exit(lines: List[str]) -> List[str]:
    out: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("if (") and stripped.endswith("{"):
            then_end = _find_block_end(lines, i)
            if then_end is not None and then_end + 1 < len(lines):
                else_line = lines[then_end + 1].strip()
                if else_line == "else {":
                    else_end = _find_block_end(lines, then_end + 1)
                    if else_end is not None:
                        then_body = lines[i + 1 : then_end]
                        last_then = ""
                        for t in reversed(then_body):
                            st = t.strip()
                            if st:
                                last_then = st
                                break
                        if last_then.startswith(("return ", "throw ")):
                            out.extend(lines[i : then_end + 1])
                            out.extend(lines[then_end + 2 : else_end])
                            i = else_end + 1
                            continue
        out.append(line)
        i += 1
    return out


def _recover_nested_shared_return_guard(lines: List[str]) -> List[str]:
    out: List[str] = []
    i = 0
    while i < len(lines):
        if i + 6 < len(lines):
            outer = re.match(r"^(\s*)if \((.+)\) \{$", lines[i])
            inner = re.match(r"^(\s*)if \((.+)\) \{$", lines[i + 1])
            first_return = re.match(r"^(\s*)return (.+)$", lines[i + 2])
            second_return = re.match(r"^(\s*)return (.+)$", lines[i + 5])
            if (
                outer
                and inner
                and first_return
                and second_return
                and lines[i + 3].strip() == "}"
                and lines[i + 4].strip() == "}"
                and first_return.group(2).strip() == second_return.group(2).strip()
                and inner.group(1) == outer.group(1) + "  "
                and first_return.group(1) == inner.group(1) + "  "
                and second_return.group(1) == outer.group(1)
                and lines[i + 6].strip() not in {"", "}", "else {"}
            ):
                indent = outer.group(1)
                outer_condition = outer.group(2).strip()
                inner_condition = inner.group(2).strip()
                value = first_return.group(2).strip()
                out.extend(
                    [
                        f"{indent}if (!({outer_condition}) || ({inner_condition})) {{",
                        f"{indent}  return {value}",
                        f"{indent}}}",
                    ]
                )
                i += 6
                continue
        out.append(lines[i])
        i += 1
    return out


def _convert_unused_accu_assign_to_expr(lines: List[str]) -> List[str]:
    out = lines[:]
    for i, line in enumerate(out):
        match = re.match(r"^(\s*)ACCU\s*=\s*(.+)$", line)
        if not match:
            continue
        indent, expr = match.groups()
        expr = expr.strip()
        if _is_pure_expr_level4(expr):
            continue
        if "(" not in expr and not _is_property_read_expr(expr):
            continue

        used = False
        for j in range(i + 1, len(out)):
            s = out[j].strip()
            if re.match(r"^ACCU\s*=", s):
                rhs = s.split("=", 1)[1].strip()
                if re.search(r"\bACCU\b", rhs):
                    used = True
                break
            if re.search(r"\bACCU\b", s):
                used = True
                break
            if s == "}":
                used = True
                break
            if s.startswith(("if ", "for ", "while ", "else ")) or s == "{":
                break
        if not used:
            out[i] = f"{indent}{expr}"
    return out


def _inline_simple_accu_loads_into_next_line(lines: List[str]) -> List[str]:
    out: List[str] = []
    i = 0
    while i < len(lines):
        if i + 1 < len(lines):
            s0 = lines[i].strip()
            s1 = lines[i + 1].strip()
            m_accu = re.match(r"^ACCU\s*=\s*(.+)$", s0)
            if (
                m_accu
                and re.search(r"\bACCU\b", s1)
                and not re.match(r"^ACCU\s*=", s1)
                and not s1.startswith(("if ", "while ", "for ", "else "))
            ):
                expr = m_accu.group(1).strip()
                if _is_pure_expr_level4(expr) and "ACCU" not in expr:
                    out.append(re.sub(r"\bACCU\b", lambda _match: expr, lines[i + 1]))
                    i += 2
                    continue
        out.append(lines[i])
        i += 1
    return out


def _is_property_read_expr(expr: str) -> bool:
    expr = expr.strip()
    if not expr or "ACCU" in expr:
        return False
    structure = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', '""', expr)
    if any(token in structure for token in ("(", ")", "=", "=>")):
        return False
    return bool(re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*|\[[^\]]+\])+", expr))


def _drop_duplicate_expr_before_assignment(lines: List[str]) -> List[str]:
    out: List[str] = []
    i = 0
    while i < len(lines):
        if i + 1 < len(lines):
            expr = lines[i].strip()
            next_line = lines[i + 1].strip()
            m_accu = re.match(r"^ACCU\s*=\s*(.+)$", expr)
            m_reg_dup = re.match(
                r"^(?!(?:if|while|for)\b).+?\s*=\s*(.+)$", next_line
            )
            if (
                m_accu
                and m_reg_dup
                and _is_pure_expr_level4(m_accu.group(1).strip())
                and m_accu.group(1).strip() == m_reg_dup.group(1).strip()
                and not _reads_accu_before_reassign(lines, i + 2)
            ):
                i += 1
                continue

            m_assign = re.match(
                r"^(?!(?:if|while|for)\b).+?\s*=\s*(.+)$", next_line
            )
            if (
                expr
                and m_assign
                and expr == m_assign.group(1).strip()
                and "(" in expr
                and not expr.startswith(("if ", "for ", "while ", "return ", "throw "))
            ):
                i += 1
                continue
        out.append(lines[i])
        i += 1
    return out


def _collapse_accu_store_return(lines: List[str]) -> List[str]:
    out: List[str] = []
    i = 0
    while i < len(lines):
        if i + 2 < len(lines):
            s0 = lines[i].strip()
            s1 = lines[i + 1].strip()
            s2 = lines[i + 2].strip()
            m_accu = re.match(r"^ACCU\s*=\s*(.+)$", s0)
            m_reg = re.match(r"^r\d+\s*=\s*(.+)$", s1)
            if (
                m_accu
                and m_reg
                and s2 == "return ACCU"
                and m_accu.group(1).strip() == m_reg.group(1).strip()
                and "ACCU" not in m_accu.group(1)
            ):
                indent = _extract_indent(lines[i + 2])
                out.append(f"{indent}return {m_accu.group(1).strip()}")
                i += 3
                continue
        out.append(lines[i])
        i += 1
    return out


def _collapse_accu_store(lines: List[str]) -> List[str]:
    out: List[str] = []
    i = 0
    while i < len(lines):
        if i + 1 < len(lines):
            s0 = lines[i].strip()
            s1 = lines[i + 1].strip()
            m_accu = re.match(r"^ACCU\s*=\s*(.+)$", s0)
            m_reg = re.match(r"^(r\d+)\s*=\s*ACCU$", s1)
            if m_accu and m_reg:
                expr = m_accu.group(1).strip()
                if "ACCU" not in expr and not _reads_accu_before_reassign(lines, i + 2):
                    indent = _extract_indent(lines[i + 1])
                    out.append(f"{indent}{m_reg.group(1)} = {expr}")
                    i += 2
                    continue
        out.append(lines[i])
        i += 1
    return out


def _collapse_accu_push_context(lines: List[str]) -> List[str]:
    out: List[str] = []
    i = 0
    while i < len(lines):
        if i + 1 < len(lines):
            s0 = lines[i].strip()
            s1 = lines[i + 1].strip()
            m_accu = re.match(r"^ACCU\s*=\s*(create_(?:function|block)_context\(.+\))$", s0)
            m_push = re.match(r"^(r\d+)\s*=\s*pushContext\(ACCU\)$", s1)
            if m_accu and m_push:
                indent = _extract_indent(lines[i + 1])
                out.append(f"{indent}{m_push.group(1)} = pushContext({m_accu.group(1).strip()})")
                i += 2
                continue
        out.append(lines[i])
        i += 1
    return out


def _reads_accu_before_reassign(lines: List[str], start: int) -> bool:
    for idx in range(start, len(lines)):
        stripped = lines[idx].strip()
        reassignment = re.match(r"^ACCU\s*=\s*(.+)$", stripped)
        if reassignment:
            if re.search(r"\bACCU\b", reassignment.group(1)):
                return True
            return False
        if re.search(r"\bACCU\b", stripped):
            return True
        if stripped in {"}", "else {"} or stripped.startswith(("return ", "throw ")):
            return False
    return False


def _drop_unused_pure_accu_loads(lines: List[str]) -> List[str]:
    keep = [True] * len(lines)

    for idx, line in enumerate(lines):
        stripped = line.strip()
        m_accu = re.match(r"^ACCU\s*=\s*(.+)$", stripped)
        if not m_accu:
            continue

        expr = m_accu.group(1).strip()
        if not _is_pure_expr_level4(expr):
            continue

        used = False
        for next_idx in range(idx + 1, len(lines)):
            next_stripped = lines[next_idx].strip()
            if next_stripped in {"}", "else {"}:
                used = True
                break
            next_reassignment = re.match(r"^ACCU\s*=\s*(.+)$", next_stripped)
            if next_reassignment:
                if re.search(r"\bACCU\b", next_reassignment.group(1)):
                    used = True
                break
            if re.search(r"\bACCU\b", next_stripped):
                used = True
                break

        if not used:
            keep[idx] = False

    return [line for idx, line in enumerate(lines) if keep[idx]]


def _name_async_reject_handler_exceptions(lines: List[str]) -> List[str]:
    out = lines[:]
    for idx, line in enumerate(out):
        stripped = line.strip()
        match = re.match(r"^(r\d+)\s*=\s*ACCU$", stripped)
        if not match:
            continue
        target_reg = match.group(1)
        pending_idx = _next_significant_index(out, idx + 1)
        if pending_idx is None or out[pending_idx].strip() != "// SetPendingMessage":
            continue
        if not _async_reject_uses_reg(out, pending_idx + 1, target_reg):
            continue
        indent = _extract_indent(line)
        out[idx] = f"{indent}{target_reg} = async_reject_exception"
    return out


def _next_significant_index(lines: List[str], start: int) -> int | None:
    for idx in range(start, len(lines)):
        if lines[idx].strip():
            return idx
    return None


def _async_reject_uses_reg(lines: List[str], start: int, reg: str) -> bool:
    for idx in range(start, min(len(lines), start + 6)):
        stripped = lines[idx].strip()
        if re.match(r"^ACCU\s*=", stripped):
            return False
        if stripped.startswith("return _AsyncFunctionReject(") and re.search(
            rf"\b{re.escape(reg)}\b", stripped
        ):
            return True
    return False


def _drop_unused_pure_reg_assignments(lines: List[str]) -> List[str]:
    live: set[str] = set()
    keep = [True] * len(lines)
    loop_carried_assignments = _loop_carried_assignment_indices(lines)

    for idx in range(len(lines) - 1, -1, -1):
        line = lines[idx]
        stripped = line.strip()
        match = re.match(r"^(r\d+)\s*=\s*(.+)$", stripped)
        if match:
            reg, expr = match.groups()
            rhs_regs = set(re.findall(r"\br\d+\b", expr))
            if (
                reg not in live
                and idx not in loop_carried_assignments
                and _is_pure_reg_rhs(expr.strip())
                and _has_following_executable_line(lines, idx + 1)
            ):
                keep[idx] = False
                continue
            live.discard(reg)
            live.update(rhs_regs)
            continue

        live.update(re.findall(r"\br\d+\b", stripped))

    return [line for idx, line in enumerate(lines) if keep[idx]]


def _loop_carried_assignment_indices(lines: List[str]) -> set[int]:
    protected: set[int] = set()
    for start, line in enumerate(lines):
        if not re.match(r"^\s*while\s*\(.+\)\s*\{$", line):
            continue
        end = _find_block_end(lines, start)
        if end is None:
            continue

        used_before: set[str] = set()
        for index in range(start, end + 1):
            stripped = lines[index].strip()
            assignment = re.match(r"^(r\d+)\s*=\s*(.+)$", stripped)
            if assignment:
                reg, rhs = assignment.groups()
                if reg in used_before:
                    protected.add(index)
                used_before.update(re.findall(r"\br\d+\b", rhs))
                continue
            used_before.update(re.findall(r"\br\d+\b", stripped))

        loop_registers = set(
            re.findall(r"\br\d+\b", "\n".join(lines[start : end + 1]))
        )
        for reg in loop_registers:
            initializer = re.compile(rf"^\s*{re.escape(reg)}\s*=")
            for index in range(start - 1, -1, -1):
                if initializer.match(lines[index]):
                    protected.add(index)
                    break
    return protected


def _has_following_executable_line(lines: List[str], start: int) -> bool:
    for idx in range(start, len(lines)):
        stripped = lines[idx].strip()
        if not stripped or stripped in {"}", "else {"}:
            continue
        return True
    return False


def _count_reg_uses(lines: List[str]) -> Dict[str, int]:
    usage: Dict[str, int] = {}
    for line in lines:
        stripped = line.strip()
        assign = re.match(r"^(r\d+)\s*=", stripped)
        lhs = assign.group(1) if assign else None
        for reg in re.findall(r"\br\d+\b", stripped):
            if reg == lhs and stripped.startswith(f"{reg} ="):
                continue
            usage[reg] = usage.get(reg, 0) + 1
    return usage


def _is_pure_reg_rhs(expr: str) -> bool:
    expr = expr.strip()
    if not expr:
        return False
    if "(" in expr:
        return False
    return _is_pure_expr_level4(expr)
