"""For-of and destructuring recovery transforms."""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Tuple

from .common import (
    _compact_compound_assignments,
    _drop_unused_reg_assignments,
    _extract_indent,
    _find_block_end,
)

from .iteration_for_in import _recover_for_in_until_stable
from .iteration_spread import _recover_array_spread_appends_until_stable

def _parse_iter_setup(lines: List[str]) -> Optional[Tuple[int, int, str, str, str]]:
    for i in range(len(lines) - 3):
        s0 = lines[i].strip()
        s1 = lines[i + 1].strip()
        s2 = lines[i + 2].strip()
        s3 = lines[i + 3].strip()

        if not s0.startswith("ACCU = GetIterator(") or not s0.endswith(")"):
            continue
        source = s0[len("ACCU = GetIterator(") : -1]
        m1 = re.match(r"^(r\d+) = GetIterator\((.+)\)$", s1)
        if not m1:
            continue
        iter_reg = m1.group(1)
        if m1.group(2) != source:
            continue
        m2 = re.match(rf"^ACCU = {re.escape(iter_reg)}\.next$", s2)
        m3 = re.match(rf"^(r\d+) = {re.escape(iter_reg)}\.next$", s3)
        if not m2 or not m3:
            continue
        next_reg = m3.group(1)
        return i, i + 3, source, iter_reg, next_reg
    return None

def _recover_for_of(lines: List[str]) -> List[str]:
    direct = _recover_direct_for_of(lines)
    if direct != lines:
        return direct
    explicit = _recover_explicit_for_of(lines)
    if explicit != lines:
        return explicit

    setup = _parse_iter_setup(lines)
    if not setup:
        return lines
    setup_start, setup_end, source, iter_reg, next_reg = setup

    while_idx = None
    while_end = None
    for i in range(setup_end + 1, len(lines)):
        if lines[i].strip() != "while (!(truthy(ACCU))) {":
            continue
        end = _find_block_end(lines, i)
        if end is None:
            continue
        while_idx = i
        while_end = end
        break
    if while_idx is None or while_end is None:
        return lines

    body = lines[while_idx + 1 : while_end]
    next_call_a = f"ACCU = {next_reg}.call({iter_reg})"
    next_call_b = re.compile(rf"^(r\d+) = {re.escape(next_reg)}\.call\({re.escape(iter_reg)}\)$")
    result_reg = None
    for line in body:
        s = line.strip()
        if next_call_a == s:
            continue
        m = next_call_b.match(s)
        if m:
            result_reg = m.group(1)
            break
    if not result_reg:
        return lines

    flag_reg = None
    for line in body:
        m_flag = re.match(r"^(r\d+) = true$", line.strip())
        if m_flag:
            flag_reg = m_flag.group(1)
            break

    inner_if_start = None
    inner_if_end = None
    for idx in range(while_idx + 1, while_end):
        if lines[idx].strip() == "if (!(truthy(ACCU))) {":
            end = _find_block_end(lines, idx)
            if end is None or end > while_end:
                continue
            inner_if_start = idx
            inner_if_end = end
            break
    if inner_if_start is None or inner_if_end is None:
        return lines

    inner_body = lines[inner_if_start + 1 : inner_if_end]
    loop_var = None
    assign_re = re.compile(rf"^(r\d+) = {re.escape(result_reg)}$")
    for line in inner_body:
        m = assign_re.match(line.strip())
        if m:
            loop_var = m.group(1)
            break
    if not loop_var:
        loop_var = "item"

    filtered: List[str] = []
    for line in inner_body:
        s = line.strip()
        if s in {"ACCU = false", "ACCU = true"}:
            continue
        if s == f"{loop_var} = {result_reg}":
            continue
        if s in {f"r6 = false", f"r6 = true"}:
            continue
        if s == f"ACCU = {result_reg}.value":
            s = f"ACCU = {loop_var}"
        elif s == f"{result_reg} = {result_reg}.value":
            continue
        elif s == f"{loop_var} = {result_reg}":
            continue
        if f"{result_reg}.value" in s:
            s = re.sub(rf"\b{re.escape(result_reg)}\.value\b", loop_var, s)
        if s.startswith(f"{loop_var} ="):
            continue
        m_noop = re.match(r"^(r\d+) = \1$", s)
        if m_noop:
            continue
        filtered.append(s)
    filtered = _compact_compound_assignments(filtered)
    filtered = _drop_unused_reg_assignments(filtered)

    loop_var_name = "item"
    body_text = "\n".join(filtered)
    if re.search(r"\bitem\b", body_text):
        loop_var_name = "item1"
    if loop_var_name != loop_var:
        filtered = [
            re.sub(rf"\b{re.escape(loop_var)}\b", loop_var_name, stmt) for stmt in filtered
        ]

    if result_reg != loop_var_name:
        needs_alias = any(re.search(rf"\b{re.escape(result_reg)}\b", stmt) for stmt in filtered)
        if needs_alias:
            filtered.insert(0, f"{result_reg} = {loop_var_name}")

    while_indent = _extract_indent(lines[while_idx])
    body_indent = while_indent + "  "
    replacement = [f"{while_indent}for (const {loop_var_name} of {source}) {{"]
    for stmt in filtered:
        replacement.append(f"{body_indent}{stmt}")
    replacement.append(f"{while_indent}}}")

    out: List[str] = []
    skip_ranges: List[Tuple[int, int]] = []
    if flag_reg:
        for idx in range(while_end + 1, len(lines) - 1):
            if lines[idx].strip() != f"ACCU = {flag_reg}":
                continue
            if lines[idx + 1].strip() != "if (!(truthy(ACCU))) {":
                continue
            end = _find_block_end(lines, idx + 1)
            if end is None:
                continue
            block_text = "\n".join(lines[idx + 1 : end + 1])
            if f"{iter_reg}.return" not in block_text:
                continue
            start = idx
            if idx >= 4:
                p0 = lines[idx - 4].strip()
                p1 = lines[idx - 3].strip()
                p2 = lines[idx - 2].strip()
                p3 = lines[idx - 1].strip()
                if (
                    p0 == "ACCU = -1"
                    and re.match(r"^r\d+ = -1$", p1)
                    and re.match(r"^r\d+ = -1$", p2)
                    and p3.startswith("// goto offset_")
                ):
                    start = idx - 4
            skip_ranges.append((start, end))
            break

    def in_skip_ranges(i: int) -> bool:
        for a, b in skip_ranges:
            if a <= i <= b:
                return True
        return False

    for idx, line in enumerate(lines):
        if in_skip_ranges(idx):
            continue
        if setup_start <= idx <= setup_end:
            continue
        if idx == while_idx:
            out.extend(replacement)
            continue
        if while_idx < idx <= while_end:
            continue
        out.append(line)
    return out


def _recover_direct_for_of(lines: List[str]) -> List[str]:
    """Recover the V8 10.x iterator skeleton after level-3 propagation.

    In this form GetIterator is stored through ACCU, while the cached `next`
    method and each iterator result are emitted as duplicated ACCU/register
    expressions.  Requiring the complete setup, `done` guard, and canonical
    IteratorClose tail keeps this rewrite tied to bytecode evidence.
    """
    for setup_start, line in enumerate(lines):
        stripped = line.strip()
        source = _direct_iterator_source(stripped)
        if source is None:
            continue

        cursor = setup_start + 1
        if cursor < len(lines) and lines[cursor].strip() == "if (!(isJSReceiver(ACCU))) {":
            guard_end = _find_block_end(lines, cursor)
            if guard_end is None:
                continue
            guard_text = "\n".join(lines[cursor : guard_end + 1])
            if (
                "ThrowSymbolIteratorInvalid" not in guard_text
                and "throw new TypeError()" not in guard_text
            ):
                continue
            cursor = guard_end + 1

        if cursor >= len(lines):
            continue
        iterator_store = re.match(r"^(r\d+) = ACCU$", lines[cursor].strip())
        if not iterator_store:
            continue
        iter_reg = iterator_store.group(1)
        cursor += 1

        next_reg: Optional[str] = None
        if cursor + 1 < len(lines) and lines[cursor].strip() == f"ACCU = {iter_reg}.next":
            next_line = lines[cursor + 1].strip()
            next_store = re.match(
                rf"^(r\d+) = {re.escape(iter_reg)}\.next$",
                next_line,
            ) or re.match(r"^(r\d+) = ACCU$", next_line)
            if not next_store:
                continue
            next_reg = next_store.group(1)
            cursor += 2

        while_idx = None
        bottom_tested = False
        for candidate in range(cursor, min(cursor + 10, len(lines))):
            stripped_header = lines[candidate].strip()
            if stripped_header == "while (!(truthy(ACCU))) {":
                while_idx = candidate
                break
            # Async functions keep V8's bottom-tested loop shape: the exit
            # test stays mid-body as `if (truthy(X.done)) { break }`.
            if stripped_header == "while (true) {":
                while_idx = candidate
                bottom_tested = True
                break
        if while_idx is None:
            continue
        while_end = _find_block_end(lines, while_idx)
        if while_end is None:
            continue

        state_lines = [item.strip() for item in lines[cursor:while_idx]]
        flag_match = next(
            (
                re.match(r"^(r\d+) = false$", item)
                for item in state_lines
                if re.match(r"^r\d+ = false$", item)
            ),
            None,
        )
        flag_reg = flag_match.group(1) if flag_match else None

        parsed_body = _parse_direct_for_of_body(
            lines,
            while_idx,
            while_end,
            iter_reg,
            next_reg,
            flag_reg,
            bottom_tested,
        )
        if parsed_body is None:
            continue
        body_lines, aliases, flag_reg = parsed_body

        cleanup = _find_direct_for_of_cleanup(
            lines, while_end + 1, iter_reg, flag_reg
        )
        if cleanup is None:
            continue
        cleanup_end, return_status = cleanup
        if return_status:
            body_lines = _recover_for_of_return_completion(body_lines, return_status)

        loop_var = "item"
        body_text = "\n".join(body_lines)
        suffix = 1
        while re.search(rf"\b{re.escape(loop_var)}\b", body_text):
            loop_var = f"item{suffix}"
            suffix += 1

        for alias in aliases:
            cleaned: List[str] = []
            for item in body_lines:
                # V8 reuses the result register as a scratch temp (`rX = rY`
                # with a non-result RHS). After folding, that store would
                # corrupt the loop variable, and it is dead anyway.
                store = re.match(
                    rf"^({re.escape(alias)}) = ([^=]+)$", item.strip()
                )
                if (
                    store
                    and not re.search(rf"\b{re.escape(alias)}\b", store.group(2))
                    and not store.group(2).startswith("ACCU")
                ):
                    continue
                cleaned.append(
                    re.sub(rf"\b{re.escape(alias)}\b", loop_var, item)
                )
            body_lines = cleaned

        indent = _extract_indent(lines[while_idx])
        replacement = [f"{indent}for (const {loop_var} of {source}) {{"]
        replacement.extend(f"{indent}  {item.strip()}" for item in body_lines)
        replacement.append(f"{indent}}}")
        return lines[:setup_start] + replacement + lines[cleanup_end + 1 :]

    return lines


def _skip_jsreceiver_guard(
    lines: List[str], index: int, subject: str
) -> Optional[int]:
    """Consume an IteratorGet-protection guard; return the line after it."""
    if index >= len(lines):
        return None
    stripped = lines[index].strip()
    lambda_guard = (
        f"if (!(((value) => Object(value) === value)({subject}))) {{"
    )
    simple_guard = f"if (!(isJSReceiver({subject}))) {{"
    accu_guard = "if (!(isJSReceiver(ACCU))) {"
    if stripped not in {lambda_guard, simple_guard, accu_guard}:
        return None
    end = _find_block_end(lines, index)
    if end is None:
        return None
    body = "\n".join(lines[index : end + 1])
    if "throw new TypeError()" not in body and "TypeError" not in body:
        return None
    return end + 1


def _parse_explicit_pair_pull(
    body: List[str], index: int, iterator: str, done_flag: Optional[str]
) -> Optional[Tuple[int, str, Optional[str]]]:
    """Parse one `iterator.next()` result extraction feeding a target temp.

    Handles the ACCU-threaded engine form (`ACCU = it.next()`, `rX = ACCU`)
    and its register-direct variants, wrapped either bare or inside an
    ``if (!(false)) { ... }`` block with an undefined-fallback else. A None
    ``done_flag`` accepts any flag store and returns the one seen. Returns
    (index after, target, flag).
    """
    if index >= len(body):
        return None
    if body[index].strip() == "if (!(false)) {":
        pull_end = _find_block_end(body, index)
        if pull_end is None:
            return None
        pull = [line.strip() for line in body[index + 1 : pull_end]]
        tail_index = pull_end + 1
    else:
        header = re.match(
            rf"^(?:(?:[\w$]+|ACCU) = )?{re.escape(iterator)}\.next\(\)$",
            body[index].strip(),
        )
        if not header:
            return None
        end = index
        while end < len(body):
            stripped = body[end].strip()
            if stripped == "}" and end + 1 < len(body) and body[end + 1].strip() == "else {":
                else_end = _find_block_end(body, end + 1)
                if else_end is not None:
                    end = else_end
                    break
            end += 1
        if end >= len(body):
            return None
        pull = [line.strip() for line in body[index : end + 1]]
        tail_index = end + 1

    cursor = 0

    def expect(pattern: str) -> Optional[re.Match]:
        nonlocal cursor
        if cursor >= len(pull):
            return None
        match = re.match(pattern, pull[cursor])
        if match:
            cursor += 1
        return match

    next_reg = expect(rf"^([\w$]+|ACCU) = {re.escape(iterator)}\.next\(\)$")
    if not next_reg:
        return None
    raw = next_reg.group(1)
    alias_match = (
        re.match(rf"^([\w$]+) = {raw}$", pull[cursor].strip())
        if raw != "ACCU" and cursor < len(pull)
        else None
    )
    subject = alias_match.group(1) if alias_match else ("r?" if raw == "ACCU" else raw)
    if raw == "ACCU":
        # `ACCU = it.next()` must be followed by a register copy.
        copy_match = expect(rf"^([\w$]+) = ACCU$")
        if not copy_match:
            return None
        subject = copy_match.group(1)
    elif alias_match:
        cursor += 1
    guard = (
        _skip_jsreceiver_guard(pull, cursor, subject)
        or (raw != "ACCU" and _skip_jsreceiver_guard(pull, cursor, raw))
        or None
    )
    if guard is None:
        return None
    cursor = guard

    # done check: negative arm carries the value; positive arm carries break
    # with the value extracted after the block.
    positive = None
    taken = expect(rf"^if \(!\({re.escape(subject)}\.done\)\) \{{$")
    if not taken:
        taken = expect(rf"^if \(!\(truthy\({re.escape(subject)}\.done\)\)\) \{{$")
    if not taken and cursor < len(pull):
        accu_done = re.match(rf"^ACCU = {re.escape(subject)}\.done$", pull[cursor].strip())
        if accu_done:
            cursor += 1
            taken = expect(rf"^if \(!\(truthy\(ACCU\)\)\) \{{$") or expect(
                rf"^if \(!\(ACCU\)\) \{{$"
            )
    if not taken and cursor < len(pull):
        done_store = re.match(rf"^[\w$]+ = {re.escape(subject)}\.done$", pull[cursor].strip())
        if done_store:
            cursor += 1
        taken = expect(rf"^if \(!\({re.escape(subject)}\.done\)\) \{{$") or expect(
            rf"^if \(!\(truthy\({re.escape(subject)}\.done\)\)\) \{{$"
        )
    if not taken and cursor < len(pull):
        positive = expect(rf"^if \(truthy\({re.escape(subject)}\.done\)\) \{{$") or expect(
            rf"^if \({re.escape(subject)}\.done\) \{{$"
        )
    if not taken and not positive:
        return None

    target: Optional[str] = None
    seen_flag: Optional[str] = done_flag
    rest: List[str] = []
    if positive:
        pos_end = _find_block_end(pull, cursor - 1)
        if pos_end is None:
            return None
        pos_body = [line.strip() for line in pull[cursor : pos_end]]
        if pos_body != ["break"]:
            return None
        cursor = pos_end + 1
        value_line = re.match(rf"^([\w$]+) = {re.escape(subject)}\.value$", pull[cursor].strip())
        if not value_line:
            return None
        target = value_line.group(1)
        cursor += 1
        while cursor < len(pull):
            line = pull[cursor]
            flag_line = re.match(r"^([\w$]+) = false$", line)
            if flag_line and (done_flag is None or flag_line.group(1) == done_flag):
                seen_flag = flag_line.group(1)
                cursor += 1
                continue
            break
    else:
        taken_index = cursor - 1
        taken_end = _find_block_end(pull, taken_index)
        if taken_end is None:
            return None
        arm = [line.strip() for line in pull[taken_index + 1 : taken_end]]
        value_store = (
            re.match(rf"^([\w$]+) = {re.escape(subject)}\.value$", arm[0]) if arm else None
        )
        if not value_store:
            return None
        target = value_store.group(1)
        cursor = taken_end + 1
        while cursor < len(pull):
            line = pull[cursor]
            flag_line = re.match(r"^([\w$]+) = false$", line)
            if flag_line and (done_flag is None or flag_line.group(1) == done_flag):
                seen_flag = flag_line.group(1)
                cursor += 1
                continue
            rest.append(line.strip())
            cursor += 1
        rest = [
            line
            for line in rest
            if line != f"{target} = {subject}"
        ]

    has_fallback = cursor < len(pull) and pull[cursor].strip() == "else {"
    if has_fallback:
        else_end = _find_block_end(pull, cursor)
        if else_end is None:
            return None
        if [line.strip() for line in pull[cursor + 1 : else_end]] != [f"{target} = undefined"]:
            return None
        cursor = else_end + 1
        trailing_else = _parse_undefined_else(pull, cursor, target)
        if trailing_else is not None:
            cursor = trailing_else
    if cursor != len(pull):
        return None
    if done_flag is not None and seen_flag != done_flag:
        return None
    return tail_index, target, seen_flag


def _parse_explicit_destructuring(
    body: List[str], index: int, iterator: str
) -> Optional[Tuple[int, str, str]]:
    """Parse V8's two-pull array-destructuring over `iterator` ([k, v])."""
    if index >= len(body):
        return None
    cursor = index
    # The done flag may be pre-initialized before the first pull or only
    # set inside it; in the latter case the first pull names it.
    probe = _parse_explicit_pair_pull(body, cursor, iterator, None)
    if probe is None:
        flag_store = re.match(r"^([\w$]+) = false$", body[cursor].strip())
        if not flag_store:
            return None
        done_flag = flag_store.group(1)
        cursor += 1
        first = _parse_explicit_pair_pull(body, cursor, iterator, done_flag)
        if first is None:
            return None
        cursor, first_target, _ = first
    else:
        cursor, first_target, done_flag = probe
    # The whole conditional pull may carry an outer `else` re-storing the
    # undefined fallback; both arms converge on the same register.
    outer_else = _parse_undefined_else(body, cursor, first_target)
    if outer_else is not None:
        cursor = outer_else
    key_store = re.match(r"^([\w$]+) = " + re.escape(first_target) + r"$", body[cursor].strip())
    if not key_store:
        return None
    key_reg = key_store.group(1)
    cursor += 1
    if cursor >= len(body) or body[cursor].strip() != f"if (!({done_flag})) {{":
        return None
    second_end = _find_block_end(body, cursor)
    if second_end is None:
        return None
    second_block = [line.strip() for line in body[cursor + 1 : second_end]]
    expected_head = f"{done_flag} = true"
    if not second_block or second_block[0] != expected_head:
        return None
    # sb[1:] is the nested conditional pull, already self-balanced.
    second = _parse_explicit_pair_pull(second_block[1:], 0, iterator, done_flag)
    if second is None:
        return None
    _, second_target, _ = second
    cursor = second_end + 1
    outer_else = _parse_undefined_else(body, cursor, second_target)
    if outer_else is not None:
        cursor = outer_else
    if cursor >= len(body):
        return None
    value_store = re.match(r"^([\w$]+) = " + re.escape(second_target) + r"$", body[cursor].strip())
    if not value_store:
        return None
    value_reg = value_store.group(1)
    cursor += 1
    # The IteratorClose may sit directly here or after status-sentinel
    # stores emitted between the destructuring and the close.
    close = _parse_iterator_close_if(body, cursor, done_flag, iterator)
    while close is None and cursor < len(body):
        stripped = body[cursor].strip()
        if not re.match(r"^[\w$]+ = (-?\d+|undefined)$", stripped):
            break
        cursor += 1
        close = _parse_iterator_close_if(body, cursor, done_flag, iterator)
    if close is None:
        return None
    return close, key_reg, value_reg


def _parse_undefined_else(
    body: List[str], index: int, target: str
) -> Optional[int]:
    """Match `else { target = undefined }`; return the line after it."""
    if index >= len(body) or body[index].strip() != "else {":
        return None
    end = _find_block_end(body, index)
    if end is None:
        return None
    if [line.strip() for line in body[index + 1 : end]] != [f"{target} = undefined"]:
        return None
    return end + 1


def _parse_iterator_close_if(
    body: List[str], index: int, condition: str, iterator: str
) -> Optional[int]:
    """Parse `if (!(cond)) { iterator.return ... }`; return the line after."""
    if index >= len(body) or body[index].strip() != f"if (!({condition})) {{":
        return None
    end = _find_block_end(body, index)
    if end is None:
        return None
    block = "\n".join(body[index : end + 1])
    if f"{iterator}.return" not in block:
        return None
    if "TypeError" not in block and "isJSReceiver" not in block:
        return None
    return end + 1


def _wrap_empty_else_body(
    body: List[str], index: int
) -> Tuple[Optional[int], List[str]]:
    """Match `if (status === 0) {` (empty) whose else holds the live body."""
    if index >= len(body):
        return None, []
    header = re.match(r"^if \(([\w$]+) === 0\) \{$", body[index].strip())
    if not header:
        return None, []
    end = _find_block_end(body, index)
    if end is None:
        return None, []
    inner = [line.strip() for line in body[index + 1 : end]]
    if inner:
        return None, []
    if end + 1 >= len(body) or body[end + 1].strip() != "else {":
        return None, []
    else_end = _find_block_end(body, end + 1)
    if else_end is None:
        return None, []
    return (
        else_end,
        [line.strip() for line in body[end + 2 : else_end]],
    )


def _parse_explicit_for_of_body(
    body: List[str], iterator: str
) -> Optional[Tuple[List[str], List[str], Optional[str], Optional[Tuple[str, str]]]]:
    """Parse the residual explicit iterator loop left by level-4 output.

    V8 reuses registers freely: the receiver guard may test the raw ``.next()``
    result while ``done``/``value`` are read through a later alias. Accept any
    consistent mix of the two registers. Returns
    (live_body, item_alias_registers, done_flag, (key, value) regs).
    """
    cursor = 0
    next_call = re.match(rf"^([\w$]+) = {re.escape(iterator)}\.next\(\)$", body[0].strip())
    if not next_call:
        return None
    result_reg = next_call.group(1)
    cursor = 1

    alias: Optional[str] = None
    alias_match = re.match(rf"^([\w$]+) = {result_reg}$", body[cursor].strip())
    if alias_match:
        alias = alias_match.group(1)
        cursor += 1
    # The receiver guard may test either register regardless of the copy
    # order between the raw result and its alias.
    subjects = (alias, result_reg) if alias else (result_reg,)
    for subject in subjects:
        guard = _skip_jsreceiver_guard(body, cursor, subject)
        if guard is not None:
            break
    if guard is None:
        return None
    cursor = guard
    cursor = guard

    def done_subject() -> Optional[str]:
        for candidate in (alias, result_reg):
            if candidate and re.match(
                rf"^[\w$]+ = {re.escape(candidate)}\.done$",
                body[cursor].strip(),
            ):
                return candidate
        return None

    subject = done_subject()
    if subject is None:
        return None
    done_store = re.match(rf"^[\w$]+ = {re.escape(subject)}\.done$", body[cursor].strip())
    if done_store:
        cursor += 1
    if cursor >= len(body) or body[cursor].strip() != f"if ({subject}.done) {{":
        return None
    break_end = _find_block_end(body, cursor)
    if break_end is None:
        return None
    if [line.strip() for line in body[cursor + 1 : break_end]] != ["break"]:
        return None
    cursor = break_end + 1
    value_store = re.match(rf"^([\w$]+) = {re.escape(subject)}\.value$", body[cursor].strip())
    if not value_store:
        return None
    value_holder = value_store.group(1)
    cursor += 1
    flag_reg: Optional[str] = None
    flag_store = re.match(r"^([\w$]+) = false$", body[cursor].strip())
    if flag_store:
        flag_reg = flag_store.group(1)
        cursor += 1

    pair: Optional[Tuple[str, str]] = None
    inner_setup = re.match(
        rf"^([\w$]+) = {re.escape(value_holder)}\[Symbol\.iterator\]\(\)$",
        body[cursor].strip(),
    ) if cursor < len(body) else None
    if inner_setup:
        inner_tmp = inner_setup.group(1)
        guard_at = _skip_jsreceiver_guard(body, cursor + 1, inner_tmp)
        if guard_at is None:
            return None
        inner_store = re.match(
            rf"^([\w$]+) = {inner_tmp}$", body[guard_at].strip()
        )
        if not inner_store:
            return None
        inner = inner_store.group(1)
        parsed = _parse_explicit_destructuring(body, guard_at + 1, inner)
        if parsed is None:
            return None
        cursor, key_reg, value_reg = parsed
        pair = (key_reg, value_reg)

    status_reg: Optional[str] = None
    tail_status = re.match(r"^([\w$]+) = -1$", body[cursor].strip()) if cursor < len(body) else None
    if tail_status:
        status_reg = tail_status.group(1)
        undefined_store = re.match(
            rf"^([\w$]+) = {re.escape(status_reg)}$", body[cursor + 1].strip()
        ) if cursor + 1 < len(body) else None
        undefined_reg = undefined_store.group(1) if undefined_store else None
        close_at = cursor + (2 if undefined_reg else 1)
        close = _parse_iterator_close_if(body, close_at, flag_reg, iterator)
        if close is None:
            return None
        cursor = close
        load_store = re.match(
            rf"^[\w$]+ = {undefined_reg}$", body[cursor].strip()
        ) if undefined_reg and cursor < len(body) else None
        if load_store:
            cursor += 1
        wrapped_end, wrapped_body = _wrap_empty_else_body(body, cursor)
        if wrapped_end is None or wrapped_end != len(body):
            return None
        return wrapped_body, [value_holder], flag_reg, pair

    return body[cursor:], [value_holder], flag_reg, pair


def _parse_explicit_outer_cleanup(
    lines: List[str], start: int, iterator: str, flag_reg: Optional[str]
) -> Optional[int]:
    """Match the post-loop IteratorClose epilogue; return its last line."""
    cursor = start
    status_store = re.match(r"^([\w$]+) = -1$", lines[cursor].strip())
    if not status_store:
        return None
    status_reg = status_store.group(1)
    copy_store = re.match(rf"^([\w$]+) = {status_reg}$", lines[cursor + 1].strip())
    if not copy_store:
        return None
    copy_reg = copy_store.group(1)
    cursor += 2
    undefined_store = re.match(r"^([\w$]+) = undefined$", lines[cursor].strip())
    if not undefined_store:
        return None
    undefined_reg = undefined_store.group(1)
    cursor += 1
    if flag_reg is None:
        return None
    close = _parse_iterator_close_if(lines, cursor, flag_reg, iterator)
    if close is None:
        return None
    cursor = close
    if cursor < len(lines):
        load_store = re.match(
            rf"^[\w$]+ = {undefined_reg}$", lines[cursor].strip()
        )
        if load_store:
            cursor += 1
    if cursor >= len(lines):
        return None
    throw = re.match(rf"^if \({copy_reg} === 0\) \{{$", lines[cursor].strip())
    if not throw:
        return None
    throw_end = _find_block_end(lines, cursor)
    if throw_end is None:
        return None
    block = "\n".join(lines[cursor : throw_end + 1])
    if f"throw {status_reg}" not in block:
        return None
    return throw_end


def _fresh_name(base: str, used: Iterable[str]) -> str:
    name = base
    suffix = 1
    while name in used:
        name = f"{base}{suffix}"
        suffix += 1
    return name


def _recover_explicit_for_of(lines: List[str]) -> List[str]:
    """Fold the explicit iterator-protocol loop into `for (const x of src)`.

    Level-4 output can leave the full protocol expansion (manual
    ``[Symbol.iterator]()``, ``.next()``/``done`` plumbing, duplicated
    IteratorResult guards, paired destructuring, IteratorClose sentinels).
    Requiring every structural piece keeps the rewrite tied to bytecode
    evidence; anything that deviates is left untouched.
    """
    for setup_start, line in enumerate(lines):
        setup = re.match(r"^([\w$]+) = (.+)\[Symbol\.iterator\]\(\)$", line.strip())
        if not setup:
            continue
        tmp, source = setup.groups()
        # The GetIterator result may be stored through a receiver guard block,
        # directly on the next line, or simply used as-is (earlier cleanup
        # passes fold the guard away). All three carry the same bytecode
        # evidence: the [Symbol.iterator]() call itself.
        guard_at = _skip_jsreceiver_guard(lines, setup_start + 1, tmp)
        start_after_setup = guard_at if guard_at is not None else setup_start + 1
        iter_store = re.match(
            rf"^([\w$]+) = {re.escape(tmp)}$", lines[start_after_setup].strip()
        )
        if iter_store:
            iterator = iter_store.group(1)
            cursor = start_after_setup + 1
        else:
            iterator = tmp
            cursor = start_after_setup
        flag_store = re.match(r"^([\w$]+) = false$", lines[cursor].strip())
        flag_reg = flag_store.group(1) if flag_store else None
        if flag_store:
            cursor += 1
        if lines[cursor].strip() != "for (;;) {":
            continue
        loop_end = _find_block_end(lines, cursor)
        if loop_end is None:
            continue
        body = [item.strip() for item in lines[cursor + 1 : loop_end]]
        parsed = _parse_explicit_for_of_body(body, iterator)
        if parsed is None:
            continue
        live_body, aliases, body_flag, pair = parsed
        if flag_reg is None:
            flag_reg = body_flag
        cleanup_end = (
            _parse_explicit_outer_cleanup(lines, loop_end + 1, iterator, flag_reg)
            if flag_reg
            else None
        )

        # The status-sentinel plumbing (`X = status; if (status === 0) {}`)
        # is dead scaffolding once the loop folds; drop the empty if/else
        # wrapper and the load of the undefined sentinel register.
        cleaned: List[str] = []
        index = 0
        while index < len(live_body):
            line = live_body[index]
            sentinel_load = re.match(r"^[\w$]+ = [\w$]+$", line.strip())
            if (
                sentinel_load
                and index + 1 < len(live_body)
                and re.match(r"^if \([\w$]+ === 0\) \{$", live_body[index + 1].strip())
            ):
                header_index = index + 1
                header_end = _find_block_end(live_body, header_index)
                if header_end is not None and header_end + 1 < len(live_body) \
                        and live_body[header_end + 1].strip() == "else {":
                    else_end = _find_block_end(live_body, header_end + 1)
                    if else_end is not None:
                        cleaned.extend(live_body[header_end + 2 : else_end])
                        index = else_end + 1
                        continue
            cleaned.append(line)
            index += 1
        live_body = cleaned

        used = set(re.findall(r"\b[A-Za-z_$][\w$]*\b", "\n".join(live_body)))
        used |= set(re.findall(r"\b[A-Za-z_$][\w$]*\b", source))
        if pair:
            first = _fresh_name("key", used)
            second = _fresh_name("value", used | {first})
            binding = f"[{first}, {second}]"
        else:
            first = _fresh_name("item", used)
            second = None
            binding = first
        for alias in aliases:
            live_body = [
                re.sub(rf"\b{re.escape(alias)}\b", first, item)
                for item in live_body
            ]
        if pair:
            key_reg, value_reg = pair
            live_body = [
                re.sub(rf"\b{re.escape(key_reg)}\b", first, item)
                for item in live_body
            ]
            live_body = [
                re.sub(rf"\b{re.escape(value_reg)}\b", second, item)
                for item in live_body
            ]

        indent = _extract_indent(lines[setup_start])
        replacement = [f"{indent}for (const {binding} of {source}) {{"]
        replacement.extend(f"{indent}  {item}" for item in live_body)
        replacement.append(f"{indent}}}")
        tail_start = (cleanup_end + 1) if cleanup_end is not None else (loop_end + 1)
        return lines[:setup_start] + replacement + lines[tail_start:]
    return lines



    """Recover the V8 10.x iterator skeleton after level-3 propagation.

    In this form GetIterator is stored through ACCU, while the cached `next`
    method and each iterator result are emitted as duplicated ACCU/register
    expressions.  Requiring the complete setup, `done` guard, and canonical
    IteratorClose tail keeps this rewrite tied to bytecode evidence.
    """
    for setup_start, line in enumerate(lines):
        stripped = line.strip()
        source = _direct_iterator_source(stripped)
        if source is None:
            continue

        cursor = setup_start + 1
        if cursor < len(lines) and lines[cursor].strip() == "if (!(isJSReceiver(ACCU))) {":
            guard_end = _find_block_end(lines, cursor)
            if guard_end is None:
                continue
            guard_text = "\n".join(lines[cursor : guard_end + 1])
            if (
                "ThrowSymbolIteratorInvalid" not in guard_text
                and "throw new TypeError()" not in guard_text
            ):
                continue
            cursor = guard_end + 1

        if cursor >= len(lines):
            continue
        iterator_store = re.match(r"^(r\d+) = ACCU$", lines[cursor].strip())
        if not iterator_store:
            continue
        iter_reg = iterator_store.group(1)
        cursor += 1

        next_reg: Optional[str] = None
        if cursor + 1 < len(lines) and lines[cursor].strip() == f"ACCU = {iter_reg}.next":
            next_line = lines[cursor + 1].strip()
            next_store = re.match(
                rf"^(r\d+) = {re.escape(iter_reg)}\.next$",
                next_line,
            ) or re.match(r"^(r\d+) = ACCU$", next_line)
            if not next_store:
                continue
            next_reg = next_store.group(1)
            cursor += 2

        while_idx = None
        bottom_tested = False
        for candidate in range(cursor, min(cursor + 10, len(lines))):
            stripped_header = lines[candidate].strip()
            if stripped_header == "while (!(truthy(ACCU))) {":
                while_idx = candidate
                break
            # Async functions keep V8's bottom-tested loop shape: the exit
            # test stays mid-body as `if (truthy(X.done)) { break }`.
            if stripped_header == "while (true) {":
                while_idx = candidate
                bottom_tested = True
                break
        if while_idx is None:
            continue
        while_end = _find_block_end(lines, while_idx)
        if while_end is None:
            continue

        state_lines = [item.strip() for item in lines[cursor:while_idx]]
        flag_match = next(
            (
                re.match(r"^(r\d+) = false$", item)
                for item in state_lines
                if re.match(r"^r\d+ = false$", item)
            ),
            None,
        )
        flag_reg = flag_match.group(1) if flag_match else None

        parsed_body = _parse_direct_for_of_body(
            lines,
            while_idx,
            while_end,
            iter_reg,
            next_reg,
            flag_reg,
            bottom_tested,
        )
        if parsed_body is None:
            continue
        body_lines, aliases, flag_reg = parsed_body

        cleanup = _find_direct_for_of_cleanup(
            lines, while_end + 1, iter_reg, flag_reg
        )
        if cleanup is None:
            continue
        cleanup_end, return_status = cleanup
        if return_status:
            body_lines = _recover_for_of_return_completion(body_lines, return_status)

        loop_var = "item"
        body_text = "\n".join(body_lines)
        suffix = 1
        while re.search(rf"\b{re.escape(loop_var)}\b", body_text):
            loop_var = f"item{suffix}"
            suffix += 1

        for alias in aliases:
            body_lines = [
                re.sub(rf"\b{re.escape(alias)}\b", loop_var, item)
                for item in body_lines
            ]

        indent = _extract_indent(lines[while_idx])
        replacement = [f"{indent}for (const {loop_var} of {source}) {{"]
        replacement.extend(f"{indent}  {item.strip()}" for item in body_lines)
        replacement.append(f"{indent}}}")
        return lines[:setup_start] + replacement + lines[cleanup_end + 1 :]

    return lines


def _direct_iterator_source(line: str) -> Optional[str]:
    if line.startswith("ACCU = GetIterator(") and line.endswith(")"):
        return line[len("ACCU = GetIterator(") : -1].strip()
    lowered = re.match(r"^ACCU = (.+)\[Symbol\.iterator\]\(\)$", line)
    return lowered.group(1).strip() if lowered else None


def _parse_direct_for_of_body(
    lines: List[str],
    while_idx: int,
    while_end: int,
    iter_reg: str,
    next_reg: Optional[str],
    flag_reg: Optional[str],
    bottom_tested: bool = False,
) -> Optional[Tuple[List[str], set[str], str]]:
    body_start = while_idx + 1
    skip = 0
    if bottom_tested:
        # The resume bookkeeping (`rX = context`, `flag = true`) before the
        # next() call belongs to V8's suspend/resume plumbing.
        while body_start + skip < min(body_start + 6, while_end) and re.match(
            r"^[\w$]+ = (context|true)$", lines[body_start + skip].strip()
        ):
            skip += 1
    body_start += skip
    result_reg: Optional[str] = None
    call_index: Optional[int] = None
    expected_call = (
        f"{next_reg}.call({iter_reg})"
        if next_reg is not None
        else f"{iter_reg}.next()"
    )
    for index in range(body_start, min(body_start + 12, while_end)):
        stripped = lines[index].strip()
        match = re.match(r"^(r\d+) = (.+)$", stripped)
        if match and match.group(2) == expected_call:
            result_reg = match.group(1)
            call_index = index
            break
        # ACCU-threaded form: `ACCU = it.call(...)` immediately followed by
        # `rX = ACCU` (the accumulator copy level-3 propagation may keep).
        if (
            stripped == f"ACCU = {expected_call}"
            and index + 1 < while_end
            and (store := re.match(r"^(r\d+) = ACCU$", lines[index + 1].strip()))
        ):
            result_reg = store.group(1)
            call_index = index + 1
            break
    if result_reg is None or call_index is None:
        return None

    cursor = call_index + 1
    guard_headers = {"if (!(isJSReceiver(ACCU))) {"}
    if bottom_tested:
        guard_headers.add(f"if (!(isJSReceiver({result_reg}))) {{")
    if cursor < while_end and lines[cursor].strip() in guard_headers:
        guard_end = _find_block_end(lines, cursor)
        if guard_end is None or guard_end >= while_end:
            return None
        guard_text = "\n".join(lines[cursor : guard_end + 1])
        if (
            "ThrowIteratorResultNotAnObject" not in guard_text
            and "throw new TypeError()" not in guard_text
        ):
            return None
        cursor = guard_end + 1

    if cursor >= while_end:
        return None
    done_positive = {
        f"if (truthy({result_reg}.done)) {{" ,
        f"if (({result_reg}.done)) {{",
    }
    if lines[cursor].strip() == f"ACCU = {result_reg}.done":
        cursor += 1
        if cursor >= while_end:
            return None
        header = lines[cursor].strip()
        if bottom_tested and header in {
            "if (truthy(ACCU)) {",
            f"if (truthy({result_reg}.done)) {{",
        }:
            pass
        elif not bottom_tested and header in {
            "if (!truthy(ACCU)) {",
            "if (!(truthy(ACCU))) {",
        }:
            pass
        else:
            return None
    elif lines[cursor].strip() in (
        {
            f"if (!truthy({result_reg}.done)) {{",
            f"if (!(truthy({result_reg}.done))) {{",
        }
        if not bottom_tested
        else done_positive
    ):
        pass
    else:
        return None

    if bottom_tested:
        # Positive exit branch: `if (truthy(...done)) { break }`. The live
        # body continues after this block, not inside it.
        break_block_end = _find_block_end(lines, cursor)
        if break_block_end is None or break_block_end >= while_end:
            return None
        inner = [item.strip() for item in lines[cursor + 1 : break_block_end]]
        if inner != ["break"]:
            return None
        cursor = break_block_end + 1
        if cursor >= while_end:
            return None

    value_block_end = _find_block_end(lines, cursor)
    if bottom_tested:
        # No value-block wrapper in this shape: the live body runs bare from
        # the value load to the loop end.
        if value_block_end is not None and value_block_end < while_end:
            return None
        value_body = [item.strip() for item in lines[cursor:while_end]]
        direct_value = f"{result_reg} = {result_reg}.value"
        accu_value = f"ACCU = {result_reg}.value"
        if direct_value in value_body:
            value_store = value_body.index(direct_value)
        elif (
            accu_value in value_body
            and value_body.index(accu_value) + 1 < len(value_body)
            and value_body[value_body.index(accu_value) + 1] == f"{result_reg} = ACCU"
        ):
            value_store = value_body.index(accu_value) + 1
        else:
            return None
        return _finish_direct_for_of_body(
            value_body[value_store + 1 :], result_reg, flag_reg
        )
    if value_block_end is None or value_block_end > while_end:
        return None

    value_body = [item.strip() for item in lines[cursor + 1 : value_block_end]]
    direct_value = f"{result_reg} = {result_reg}.value"
    accu_value = f"ACCU = {result_reg}.value"
    if direct_value in value_body:
        value_store = value_body.index(direct_value)
    elif (
        accu_value in value_body
        and value_body.index(accu_value) + 1 < len(value_body)
        and value_body[value_body.index(accu_value) + 1] == f"{result_reg} = ACCU"
    ):
        # ACCU-threaded pair: `ACCU = result.value` then `result = ACCU`.
        value_store = value_body.index(accu_value) + 1
    else:
        return None
    return _finish_direct_for_of_body(
        value_body[value_store + 1 :], result_reg, flag_reg
    )


def _finish_direct_for_of_body(
    tail: List[str], result_reg: str, flag_reg: Optional[str]
) -> Optional[Tuple[List[str], set[str], str]]:
    """Consume flag reset and result aliases after the value load."""
    body_cursor = 0
    if body_cursor < len(tail) and tail[body_cursor] in {
        "ACCU = false",
        f"{flag_reg} = false",
    }:
        body_cursor += 1
    body_flag = (
        re.match(r"^(r\d+) = false$", tail[body_cursor])
        if body_cursor < len(tail)
        else None
    )
    if body_flag:
        if flag_reg is not None and body_flag.group(1) != flag_reg:
            return None
        flag_reg = body_flag.group(1)
        body_cursor += 1
    if flag_reg is None:
        return None

    aliases = {result_reg}
    while body_cursor < len(tail):
        alias = re.match(
            rf"^(r\d+) = {re.escape(result_reg)}$", tail[body_cursor]
        )
        if not alias:
            break
        aliases.add(alias.group(1))
        body_cursor += 1

    return tail[body_cursor:], aliases, flag_reg
    if body_cursor < len(value_body) and value_body[body_cursor] in {
        "ACCU = false",
        f"{flag_reg} = false",
    }:
        body_cursor += 1
    body_flag = (
        re.match(r"^(r\d+) = false$", value_body[body_cursor])
        if body_cursor < len(value_body)
        else None
    )
    if body_flag:
        if flag_reg is not None and body_flag.group(1) != flag_reg:
            return None
        flag_reg = body_flag.group(1)
        body_cursor += 1
    if flag_reg is None:
        return None

    aliases = {result_reg}
    while body_cursor < len(value_body):
        alias = re.match(
            rf"^(r\d+) = {re.escape(result_reg)}$", value_body[body_cursor]
        )
        if not alias:
            break
        aliases.add(alias.group(1))
        body_cursor += 1

    return value_body[body_cursor:], aliases, flag_reg


def _find_direct_for_of_cleanup(
    lines: List[str], start: int, iter_reg: str, flag_reg: str
) -> Optional[Tuple[int, Optional[str]]]:
    limit = min(start + 45, len(lines))
    for index in range(start, limit):
        stripped = lines[index].strip()
        if stripped.startswith("// SwitchOnSmiNoFeedback ACCU"):
            status_reg = None
            if index > start:
                status = re.match(r"^ACCU = (r\d+)$", lines[index - 1].strip())
                if status:
                    status_reg = status.group(1)
            end = index
            if end + 1 < len(lines) and lines[end + 1].strip().startswith("// goto offset_"):
                end += 1
            return end, status_reg

        if stripped != "if (truthy(ACCU)) {":
            continue
        end = _find_block_end(lines, index)
        if end is None:
            continue
        block = "\n".join(lines[index : end + 1])
        if "throw ACCU" not in block:
            continue
        if index < 2:
            continue
        compare = re.match(
            r"^ACCU = \((r\d+) === ACCU\)$", lines[index - 1].strip()
        )
        if lines[index - 2].strip() != "ACCU = 0" or not compare:
            continue
        return end, None

    close_end: Optional[int] = None
    close_start: Optional[int] = None
    close_conditions = {
        f"if (!truthy({flag_reg})) {{",
        f"if (!(truthy({flag_reg}))) {{",
    }
    for index in range(start, min(start + 30, len(lines))):
        guard_index = index
        if lines[index].strip() not in close_conditions:
            if (
                lines[index].strip() == f"ACCU = {flag_reg}"
                and index + 1 < len(lines)
                and lines[index + 1].strip() in {
                    "if (!truthy(ACCU)) {",
                    "if (!(truthy(ACCU))) {",
                }
            ):
                guard_index = index + 1
            else:
                continue
        end = _find_block_end(lines, guard_index)
        if end is None:
            continue
        block = "\n".join(lines[guard_index : end + 1])
        if f"{iter_reg}.return" not in block:
            continue
        if (
            "ThrowIteratorResultNotAnObject" not in block
            and "throw new TypeError()" not in block
            and "isJSReceiver" not in block
        ):
            continue
        close_start, close_end = index, end
        break
    if close_start is None or close_end is None:
        return None

    prefix = "\n".join(lines[start:close_start])
    cleanup_entry_targets = [
        int(match.group(1))
        for match in re.finditer(r"^\s*// goto offset_(\d+)\s*$", prefix, re.MULTILINE)
    ]
    status_values = {
        match.group(1): int(match.group(2))
        for match in re.finditer(
            r"^\s*(r\d+) = (-?\d+)\s*$", prefix, re.MULTILINE
        )
    }

    def sentinel_register(register: str) -> bool:
        return not register.startswith("r") or status_values.get(register) == -1

    # Direct form: `if (rX === 0) {` (optionally with a goto-only else).
    for index in range(close_end + 1, min(close_end + 16, len(lines))):
        guard = re.match(
            r"^if \((r\d+|-?\d+) === 0\) \{$", lines[index].strip()
        )
        if not guard or not sentinel_register(guard.group(1)):
            continue
        end = _find_block_end(lines, index)
        if end is None:
            continue
        body = [item.strip() for item in lines[index + 1 : end] if item.strip()]
        if body:
            continue
        final_end = end
        if end + 1 < len(lines) and lines[end + 1].strip() == "else {":
            else_end = _find_block_end(lines, end + 1)
            if else_end is None:
                continue
            else_body = [
                item.strip() for item in lines[end + 2 : else_end] if item.strip()
            ]
            if else_body:
                continuation = (
                    re.fullmatch(r"// goto offset_(\d+)", else_body[0])
                    if len(else_body) == 1
                    else None
                )
                if (
                    continuation is None
                    or len(cleanup_entry_targets) != 1
                    or int(continuation.group(1)) <= cleanup_entry_targets[0]
                ):
                    continue
            final_end = else_end
        return final_end, None

    # ACCU-threaded form after propagation: `ACCU = 0; ACCU = (rX === ACCU);
    # if (truthy(ACCU)) { ACCU = rY; throw ACCU }`. The close epilogue ends
    # before this resume-mode check.
    window = "\n".join(lines[close_end + 1 : min(close_end + 16, len(lines))])
    accu_guard = re.search(
        r"^ACCU = 0$\n"
        r"^ACCU = \(([\w$]+) === ACCU\)$\n"
        r"^if \(truthy\(ACCU\)\) \{$\n"
        r"(?:^[^\n]*$\n)*?"
        r"^\s*throw ACCU$\n"
        r"^\}",
        window,
        re.MULTILINE,
    )
    if accu_guard and sentinel_register(accu_guard.group(1)):
        return close_end, None
    return None


def _recover_for_of_return_completion(lines: List[str], status_reg: str) -> List[str]:
    out = lines[:]
    index = 0
    while index < len(out):
        if out[index].strip() != f"{status_reg} = 1":
            index += 1
            continue

        value_index = index - 1
        if value_index >= 0 and out[value_index].strip() == "ACCU = 1":
            value_index -= 1
        if value_index < 0:
            index += 1
            continue
        value = re.match(r"^(r\d+) = (.+)$", out[value_index].strip())
        if not value or "ACCU" in value.group(2):
            index += 1
            continue

        start = value_index
        if start > 0 and out[start - 1].strip() == f"ACCU = {value.group(2)}":
            start -= 1
        out[start : index + 1] = [f"return {value.group(2)}"]
        index = start + 1

    return out


def _recover_array_destructuring_until_stable(lines: List[str]) -> List[str]:
    current = lines
    while True:
        recovered = _recover_direct_array_destructuring(current)
        if recovered == current:
            recovered = _recover_array_destructuring(current)
        if recovered == current:
            return current
        current = recovered


def _recover_direct_array_destructuring(lines: List[str]) -> List[str]:
    """Recover finite iterator consumption as an array destructuring assignment.

    V8 emits an explicit IteratorClose region after destructuring.  Requiring
    the get/next/done/value sequence, a shared completion flag, the return
    method call, and the sentinel rethrow guard keeps this transformation
    evidence driven.  A step that advances the iterator without loading value
    becomes a destructuring elision.
    """
    for start, line in enumerate(lines):
        direct = re.match(r"^ACCU = GetIterator\((.+)\)$", line.strip())
        if not direct:
            continue
        source = direct.group(1).strip()
        guard_start = start + 1
        if (
            guard_start >= len(lines)
            or lines[guard_start].strip() != "if (!(isJSReceiver(ACCU))) {"
        ):
            continue
        guard_end = _find_block_end(lines, guard_start)
        if guard_end is None or "ThrowSymbolIteratorInvalid" not in "\n".join(
            lines[guard_start : guard_end + 1]
        ):
            continue

        cursor = guard_end + 1
        iterator_store = (
            re.match(r"^(r\d+) = ACCU$", lines[cursor].strip())
            if cursor < len(lines)
            else None
        )
        if not iterator_store:
            continue
        iterator = iterator_store.group(1)
        cursor += 1

        elements: List[Optional[str]] = []
        completion_flag: Optional[str] = None
        while cursor < len(lines):
            step = _parse_direct_destructuring_step(
                lines, cursor, iterator, completion_flag
            )
            if step is None:
                break
            cursor, completion_flag, target = step
            elements.append(target)
        if not elements or completion_flag is None or all(item is None for item in elements):
            continue

        cleanup = _match_direct_destructuring_cleanup(
            lines, cursor, iterator, completion_flag
        )
        if cleanup is None:
            continue

        rendered = ", ".join(item or "" for item in elements)
        indent = _extract_indent(lines[start])
        return (
            lines[:start]
            + [f"{indent}[{rendered}] = {source}"]
            + lines[cleanup + 1 :]
        )
    return lines


def _parse_direct_destructuring_step(
    lines: List[str],
    start: int,
    iterator: str,
    expected_flag: Optional[str],
) -> Optional[Tuple[int, str, Optional[str]]]:
    if start >= len(lines):
        return None
    condition = lines[start].strip()
    if expected_flag is None:
        if condition not in {"if (!truthy(false)) {", "if (!(truthy(false))) {"}:
            return None
    elif condition not in {
        f"if (!truthy({expected_flag})) {{",
        f"if (!(truthy({expected_flag}))) {{",
    }:
        return None

    end = _find_block_end(lines, start)
    if end is None:
        return None
    final_end = end
    if end + 1 < len(lines) and lines[end + 1].strip() == "else {":
        else_end = _find_block_end(lines, end + 1)
        if else_end is None:
            return None
        else_body = [item.strip() for item in lines[end + 2 : else_end] if item.strip()]
        if else_body != ["ACCU = undefined"]:
            return None
        final_end = else_end

    body = [item.strip() for item in lines[start + 1 : end]]
    result: Optional[str] = None
    for index in range(len(body) - 1):
        if body[index] != f"ACCU = {iterator}.next()":
            continue
        match = re.match(
            rf"^(r\d+) = {re.escape(iterator)}\.next\(\)$", body[index + 1]
        )
        if match:
            result = match.group(1)
            break
    if result is None:
        return None

    flag_match = next(
        (re.match(r"^(r\d+) = false$", item) for item in body if re.match(r"^r\d+ = false$", item)),
        None,
    )
    if not flag_match:
        return None
    flag = flag_match.group(1)
    if expected_flag is not None and flag != expected_flag:
        return None

    loads_value = (
        f"{result} = {result}.value" in body and f"ACCU = {result}" in body
    )
    if not loads_value:
        if any(f"{result}.value" in item for item in body):
            return None
        return final_end + 1, flag, None

    target_index = final_end + 1
    target = (
        re.match(r"^(r\d+) = ACCU$", lines[target_index].strip())
        if target_index < len(lines)
        else None
    )
    if not target:
        return None
    return target_index + 1, flag, target.group(1)


def _match_direct_destructuring_cleanup(
    lines: List[str], start: int, iterator: str, flag: str
) -> Optional[int]:
    close_start: Optional[int] = None
    close_end: Optional[int] = None
    for index in range(start, min(start + 20, len(lines))):
        if lines[index].strip() not in {
            f"if (!truthy({flag})) {{",
            f"if (!(truthy({flag}))) {{",
        }:
            continue
        end = _find_block_end(lines, index)
        if end is None:
            continue
        block = "\n".join(lines[index : end + 1])
        if f"{iterator}.return" in block and "ThrowIteratorResultNotAnObject" in block:
            close_start, close_end = index, end
            break
    if close_start is None or close_end is None:
        return None

    prefix = "\n".join(lines[start:close_start])
    status_values = {
        match.group(1): int(match.group(2))
        for match in re.finditer(r"^\s*(r\d+) = (-?\d+)\s*$", prefix, re.MULTILINE)
    }
    for index in range(close_end + 1, min(close_end + 16, len(lines))):
        guard = re.match(r"^if \((r\d+|-?\d+) === 0\) \{$", lines[index].strip())
        if not guard:
            continue
        sentinel = guard.group(1)
        if sentinel.startswith("r"):
            if status_values.get(sentinel) != -1:
                continue
        elif int(sentinel) != -1:
            continue
        end = _find_block_end(lines, index)
        if end is None:
            continue
        body = [item.strip() for item in lines[index + 1 : end] if item.strip()]
        if len(body) == 1 and body[0].startswith("throw "):
            return end
    return None


def _recover_array_destructuring(lines: List[str]) -> List[str]:
    for start, line in enumerate(lines):
        source = _get_iterator_source_from_guard(line.strip())
        guard_start = start
        replacement_start = start
        direct = re.match(r"^ACCU = GetIterator\((.+)\)$", line.strip())
        if source is None and direct and start + 1 < len(lines):
            if lines[start + 1].strip() == "if (!(isJSReceiver(ACCU))) {":
                source = direct.group(1).strip()
                guard_start = start + 1
        if source is None:
            continue
        guard_end = _find_block_end(lines, guard_start)
        if guard_end is None:
            continue
        if "ThrowSymbolIteratorInvalid" not in "\n".join(lines[guard_start : guard_end + 1]):
            continue

        cursor = guard_end + 1
        if cursor >= len(lines):
            continue
        iterator = re.match(r"^(r\d+) = ACCU$", lines[cursor].strip())
        if not iterator:
            continue
        iter_reg = iterator.group(1)
        cursor += 1

        first = _parse_destructuring_element(lines, cursor, iter_reg)
        if first is None:
            continue
        first_end, first_flag = first
        cursor = first_end + 1
        if cursor >= len(lines):
            continue
        first_target = re.match(r"^(r\d+) = ACCU$", lines[cursor].strip())
        if not first_target:
            continue
        cursor += 1

        second = _parse_destructuring_element(lines, cursor, iter_reg, first_flag)
        if second is None:
            continue
        second_end, second_flag = second
        if second_flag != first_flag:
            continue
        cursor = second_end + 1
        if cursor >= len(lines):
            continue
        second_target = re.match(r"^(r\d+) = ACCU$", lines[cursor].strip())
        if not second_target:
            continue

        domain_if = None
        domain_else = None
        domain_end = None
        for candidate in range(cursor + 1, min(cursor + 35, len(lines))):
            condition = lines[candidate].strip()
            status_condition = re.match(r"^if \((r\d+) === 0\) \{$", condition)
            if condition != "if (-1 === 0) {" and not status_condition:
                continue
            branch_end = _find_block_end(lines, candidate)
            if branch_end is None or branch_end + 1 >= len(lines):
                continue
            if lines[branch_end + 1].strip() != "else {":
                continue
            if any(item.strip() for item in lines[candidate + 1 : branch_end]):
                continue
            else_end = _find_block_end(lines, branch_end + 1)
            if else_end is None:
                continue
            cleanup_text = "\n".join(lines[cursor + 1 : candidate])
            if status_condition and not re.search(
                rf"^\s*{re.escape(status_condition.group(1))}\s*=\s*-1\s*$",
                cleanup_text,
                re.MULTILINE,
            ):
                continue
            if (
                f"{iter_reg}.return" not in cleanup_text
                or "// SetPendingMessage" not in cleanup_text
            ):
                continue
            domain_if = candidate
            domain_else = branch_end + 1
            domain_end = else_end
            break
        if domain_if is None or domain_else is None or domain_end is None:
            continue

        domain = [item.strip() for item in lines[domain_else + 1 : domain_end]]
        names = _destructuring_names(source, "\n".join(domain))
        replacements = {
            first_target.group(1): names[0],
            second_target.group(1): names[1],
        }
        for register, name in replacements.items():
            domain = [
                re.sub(rf"\b{re.escape(register)}\b", name, item)
                for item in domain
            ]

        indent = _extract_indent(lines[replacement_start])
        replacement = [f"{indent}const [{names[0]}, {names[1]}] = {source}"]
        replacement.extend(f"{indent}{item}" for item in domain)
        return lines[:replacement_start] + replacement + lines[domain_end + 1 :]

    return lines


def _get_iterator_source_from_guard(line: str) -> Optional[str]:
    if not line.startswith("if (!(isJSReceiver(") or not line.endswith(" {" ):
        return None
    marker = "GetIterator("
    marker_start = line.find(marker)
    if marker_start < 0:
        return None
    start = marker_start + len(marker)
    depth = 1
    quote: Optional[str] = None
    escaped = False
    for index in range(start, len(line)):
        char = line[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return line[start:index].strip()
    return None


def _parse_destructuring_element(
    lines: List[str], start: int, iter_reg: str, expected_flag: Optional[str] = None
) -> Optional[Tuple[int, str]]:
    if start >= len(lines):
        return None
    condition = lines[start].strip()
    if expected_flag is None:
        if condition not in {"if (!truthy(false)) {", "if (!(truthy(false))) {"}:
            return None
    elif condition not in {
        f"if (!truthy({expected_flag})) {{",
        f"if (!(truthy({expected_flag}))) {{",
    }:
        return None

    end = _find_block_end(lines, start)
    if end is None:
        return None
    block = [item.strip() for item in lines[start + 1 : end]]
    call_index = None
    result_reg = None
    for index in range(len(block) - 1):
        if block[index] != f"ACCU = {iter_reg}.next()":
            continue
        result = re.match(
            rf"^(r\d+) = {re.escape(iter_reg)}\.next\(\)$", block[index + 1]
        )
        if result:
            call_index = index
            result_reg = result.group(1)
            break
    if call_index is None or result_reg is None:
        return None

    flag = None
    for item in block[call_index + 2 :]:
        match = re.match(r"^(r\d+) = false$", item)
        if match:
            flag = match.group(1)
            break
    if flag is None:
        return None
    if expected_flag is not None and flag != expected_flag:
        return None
    if f"{result_reg} = {result_reg}.value" not in block:
        return None
    if f"ACCU = {result_reg}" not in block:
        return None
    final_end = end
    if end + 1 < len(lines) and lines[end + 1].strip() == "else {":
        else_end = _find_block_end(lines, end + 1)
        if else_end is None:
            return None
        else_body = [item.strip() for item in lines[end + 2 : else_end] if item.strip()]
        if else_body != ["ACCU = undefined"]:
            return None
        final_end = else_end
    return final_end, flag


def _destructuring_names(source: str, body: str) -> Tuple[str, str]:
    base = "item"
    source_name = re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", source)
    if source_name:
        base = source_name.group(0)
    first = f"{base}0"
    second = f"{base}1"
    suffix = 0
    while re.search(rf"\b(?:{re.escape(first)}|{re.escape(second)})\b", body):
        suffix += 1
        first = f"{base}{suffix * 2}"
        second = f"{base}{suffix * 2 + 1}"
    return first, second

def _strip_for_of_state_initializers(lines: List[str]) -> List[str]:
    remove: set[int] = set()
    for i, line in enumerate(lines):
        if not line.strip().startswith("for ("):
            continue

        j = i - 1
        if j >= 0 and re.match(r"^\s*r\d+\s*=\s*context$", lines[j]):
            remove.add(j)
            j -= 1

        while j >= 0:
            s_cur = lines[j].strip()
            if re.match(r"^r\d+\s*=\s*(?:true|false|HOLE)$", s_cur):
                remove.add(j)
                j -= 1
                continue
            break

        while j - 1 >= 0:
            s_prev = lines[j - 1].strip()
            s_cur = lines[j].strip()
            if s_prev == "ACCU = HOLE" and re.match(r"^r\d+\s*=\s*HOLE$", s_cur):
                remove.update({j - 1, j})
                j -= 2
                continue
            if s_prev == "ACCU = false" and re.match(r"^r\d+\s*=\s*false$", s_cur):
                remove.update({j - 1, j})
                j -= 2
                continue
            break

    return [line for idx, line in enumerate(lines) if idx not in remove]

def _accu_used_before_reassign_or_block_end(lines: List[str], start: int, end: int) -> bool:
    for idx in range(start, min(end + 1, len(lines))):
        stripped = lines[idx].strip()
        reassignment = re.match(r"^ACCU\s*=\s*(.+)$", stripped)
        if reassignment:
            if re.search(r"\bACCU\b", reassignment.group(1)):
                return True
            return False
        if re.search(r"\bACCU\b", stripped):
            return True
    return False

def _reg_used_before_reassign_or_block_end(
    lines: List[str], reg: str, start: int, end: int
) -> bool:
    for idx in range(start, min(end + 1, len(lines))):
        stripped = lines[idx].strip()
        if re.match(rf"^{re.escape(reg)}\s*=", stripped):
            return False
        if re.search(rf"\b{re.escape(reg)}\b", stripped):
            return True
    return False

def _strip_for_of_recovery_noise(lines: List[str]) -> List[str]:
    remove: set[int] = set()
    active_loops: List[Tuple[int, str]] = []

    for idx, line in enumerate(lines):
        active_loops = [(end, var) for end, var in active_loops if idx <= end]
        stripped = line.strip()

        m_for = re.match(r"^for \(const ([A-Za-z_$][A-Za-z0-9_$]*) of .+\) \{$", stripped)
        if m_for:
            end = _find_block_end(lines, idx)
            if end is not None:
                active_loops.append((end, m_for.group(1)))
            continue

        if not active_loops:
            continue

        loop_end = min(end for end, _var in active_loops)
        loop_vars = {var for _end, var in active_loops}

        identity = re.match(
            r"^([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*\1$", stripped
        )
        if identity and identity.group(1) in loop_vars:
            remove.add(idx)
            continue

        m_accu = re.match(r"^ACCU\s*=\s*(.+)$", stripped)
        if m_accu and m_accu.group(1).strip() in loop_vars:
            if not _accu_used_before_reassign_or_block_end(lines, idx + 1, loop_end):
                remove.add(idx)
            continue

        m_bool = re.match(r"^(r\d+)\s*=\s*(?:true|false)$", stripped)
        if m_bool and not _reg_used_before_reassign_or_block_end(
            lines, m_bool.group(1), idx + 1, loop_end
        ):
            remove.add(idx)

    return [line for idx, line in enumerate(lines) if idx not in remove]

def _avoid_for_of_loop_var_source_collision(lines: List[str]) -> List[str]:
    out = lines[:]
    idx = 0
    while idx < len(out):
        stripped = out[idx].strip()
        m_for = re.match(
            r"^(for \(const )([A-Za-z_$][A-Za-z0-9_$]*)( of (.+)\) \{)$", stripped
        )
        if not m_for:
            idx += 1
            continue

        var = m_for.group(2)
        source = m_for.group(4)
        if not re.search(rf"\b{re.escape(var)}\b", source):
            idx += 1
            continue

        end = _find_block_end(out, idx)
        if end is None:
            idx += 1
            continue

        suffix = 1
        new_var = f"{var}{suffix}"
        block_text = "\n".join(out[idx : end + 1])
        while re.search(rf"\b{re.escape(new_var)}\b", block_text):
            suffix += 1
            new_var = f"{var}{suffix}"

        indent = _extract_indent(out[idx])
        out[idx] = f"{indent}{m_for.group(1)}{new_var}{m_for.group(3)}"
        for body_idx in range(idx + 1, end):
            out[body_idx] = re.sub(rf"\b{re.escape(var)}\b", new_var, out[body_idx])

        idx = end + 1
    return out
