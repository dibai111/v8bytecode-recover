"""Iterator, for-in and for-of recovery transforms."""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .common import (
    _compact_compound_assignments,
    _drop_unused_reg_assignments,
    _extract_indent,
    _find_block_end,
)


def _recover_array_spread_appends_until_stable(lines: List[str]) -> List[str]:
    """Lower V8's array-spread iterator loop to native JavaScript.

    Array literal spread is emitted as an iterator protocol loop which appends
    every ``IteratorResult.value`` to a monotonically increasing array index.
    This pass requires that complete data-flow skeleton; it does not infer a
    spread from helper names alone.
    """
    current = lines
    while True:
        recovered = _recover_array_spread_append(current)
        if recovered == current:
            return current
        current = recovered


def _recover_for_in_until_stable(lines: List[str]) -> List[str]:
    current = lines
    while True:
        recovered = _recover_for_in(current)
        if recovered == current:
            return current
        current = recovered


def _recover_for_in(lines: List[str]) -> List[str]:
    for start in range(len(lines) - 6):
        legacy_receiver = re.match(
            r"^Object\((r\d+)\)$", lines[start].strip()
        )
        converted_receiver = re.match(
            r"^(r\d+) = Object\((.+)\)$", lines[start].strip()
        )
        enumerate_call = re.match(
            r"^ACCU = ForInEnumerate\((r\d+)\)$", lines[start + 1].strip()
        )
        prepare = re.match(
            r"^(r\d+), (r\d+), (r\d+) = ForInPrepare\(ACCU, (\[[^]]+\])\)$",
            lines[start + 2].strip(),
        )
        if (not legacy_receiver and not converted_receiver) or not enumerate_call or not prepare:
            continue
        receiver = (
            legacy_receiver.group(1)
            if legacy_receiver
            else converted_receiver.group(1)
        )
        source = receiver if legacy_receiver else converted_receiver.group(2).strip()
        if enumerate_call.group(1) != receiver:
            continue
        cache, cache_type, cache_length, slot = prepare.groups()
        if lines[start + 3].strip() != "ACCU = 0":
            continue
        index_init = re.match(r"^(r\d+) = 0$", lines[start + 4].strip())
        if not index_init:
            continue
        index = index_init.group(1)
        while_start = start + 5
        if lines[while_start].strip() not in {
            "while (truthy(ACCU)) {",
            "while ((ACCU)) {",
            "while (ACCU) {",
        }:
            continue
        while_end = _find_block_end(lines, while_start)
        if while_end is None:
            continue

        outer_if = while_start + 1
        outer_condition = lines[outer_if].strip()
        if not re.fullmatch(
            rf"if \((?:truthy\()?\(?{re.escape(index)} !== {re.escape(cache_length)}\)?\)?\) \{{",
            outer_condition,
        ):
            continue
        outer_end = _find_block_end(lines, outer_if)
        if outer_end is None or outer_end != while_end - 1:
            continue

        next_index = outer_if + 1
        next_pattern = (
            r"ForInNext\((r\d+), (r\d+), (r\d+), (r\d+), (\[[^]]+\])\)"
        )
        next_call = re.match(
            rf"^ACCU = {next_pattern}$", lines[next_index].strip()
        )
        embedded_next = False
        value_if = next_index + 1
        if not next_call:
            next_call = re.match(
                rf"^if \(!\(({next_pattern}) === undefined\)\) \{{$",
                lines[next_index].strip(),
            )
            if next_call:
                embedded_next = True
                value_if = next_index
        if not next_call or next_call.groups() != (
            ((f"ForInNext({receiver}, {index}, {cache}, {cache_type}, {slot})", receiver, index, cache, cache_type, slot)
             if embedded_next
             else (receiver, index, cache, cache_type, slot))
        ):
            continue
        if lines[value_if].strip() not in {
            "if (!(ACCU === undefined)) {",
            "if (ACCU !== undefined) {",
        } and not embedded_next:
            continue
        value_end = _find_block_end(lines, value_if)
        if value_end is None or value_end + 1 >= outer_end:
            continue
        step_index = value_end + 1
        if lines[step_index].strip() == f"ACCU = ForInStep({index})":
            step_index += 1
        if step_index >= outer_end:
            continue
        if lines[step_index].strip() != f"{index} = ForInStep({index})":
            continue
        if step_index + 1 != outer_end:
            continue

        body = [item.strip() for item in lines[value_if + 1 : value_end]]
        aliases: List[str] = []
        while body:
            alias_rhs = (
                re.escape(
                    f"ForInNext({receiver}, {index}, {cache}, {cache_type}, {slot})"
                )
                if embedded_next
                else "ACCU"
            )
            alias = re.match(rf"^(r\d+) = {alias_rhs}$", body[0])
            if not alias:
                break
            aliases.append(alias.group(1))
            body.pop(0)
        if not aliases:
            continue

        body_text = "\n".join(body)
        key = "key"
        suffix = 1
        while re.search(rf"\b{re.escape(key)}\b", body_text):
            key = f"key{suffix}"
            suffix += 1
        for alias in aliases:
            body = [
                re.sub(rf"\b{re.escape(alias)}\b", key, item) for item in body
            ]

        indent = _extract_indent(lines[start])
        replacement = [f"{indent}for (const {key} in {source}) {{"]
        replacement.extend(f"{indent}  {item}" for item in body)
        replacement.append(f"{indent}}}")
        return lines[:start] + replacement + lines[while_end + 1 :]
    return lines


def _recover_array_spread_append(lines: List[str]) -> List[str]:
    for start, line in enumerate(lines):
        direct = re.match(r"^ACCU = GetIterator\((.+)\)$", line.strip())
        source_store = re.match(r"^(r\d+) = (.+)$", line.strip())
        symbol_iterator = (
            re.match(
                rf"^ACCU = {re.escape(source_store.group(1))}\[Symbol\.iterator\]\(\)$",
                lines[start + 1].strip(),
            )
            if source_store and start + 1 < len(lines)
            else None
        )
        if not direct and not symbol_iterator:
            continue
        source = (
            direct.group(1).strip()
            if direct
            else source_store.group(2).strip()
        )

        cursor = start + (1 if direct else 2)
        if cursor >= len(lines) or lines[cursor].strip() != "if (!(isJSReceiver(ACCU))) {":
            continue
        guard_end = _find_block_end(lines, cursor)
        guard_text = (
            "\n".join(lines[cursor : guard_end + 1])
            if guard_end is not None
            else ""
        )
        if guard_end is None or not (
            "ThrowSymbolIteratorInvalid" in guard_text
            or "throw new TypeError()" in guard_text
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
        if cursor >= len(lines) or lines[cursor].strip() != f"ACCU = {iterator}.next":
            continue
        cursor += 1

        if cursor >= len(lines) or lines[cursor].strip() != "while (!(truthy(ACCU))) {":
            continue
        loop_start = cursor
        loop_end = _find_block_end(lines, loop_start)
        if loop_end is None:
            continue
        body = [item.strip() for item in lines[loop_start + 1 : loop_end]]
        parsed = _parse_array_spread_loop_body(body, iterator)
        if parsed is None:
            continue
        array, index = parsed

        used = set(re.findall(r"\br\d+\b", "\n".join(lines[start : loop_end + 1])))
        item = "item"
        suffix = 1
        while item in used or re.search(rf"\b{re.escape(item)}\b", source):
            item = f"item{suffix}"
            suffix += 1

        indent = _extract_indent(lines[start])
        replacement = [
            f"{indent}for (const {item} of {source}) {{",
            f"{indent}  {array}[{index}] = {item}",
            f"{indent}  {index} += 1",
            f"{indent}}}",
        ]
        return lines[:start] + replacement + lines[loop_end + 1 :]
    return lines


def _parse_array_spread_loop_body(
    body: List[str], iterator: str
) -> Optional[Tuple[str, str]]:
    if len(body) < 6 or body[0] != f"ACCU = {iterator}.next()":
        return None
    result_call = re.match(
        rf"^(r\d+) = {re.escape(iterator)}\.next\(\)$", body[1]
    )
    if not result_call:
        return None
    result = result_call.group(1)
    if body[2] != "if (!(isJSReceiver(ACCU))) {":
        return None

    # Work on the loop body as a standalone balanced fragment.
    guard_end = _find_block_end(body, 2)
    guard_text = (
        "\n".join(body[2 : guard_end + 1]) if guard_end is not None else ""
    )
    if guard_end is None or not (
        "ThrowIteratorResultNotAnObject" in guard_text
        or "throw new TypeError()" in guard_text
    ):
        return None
    value_if = guard_end + 1
    if value_if >= len(body) or body[value_if] not in {
        f"if (!(truthy({result}.done))) {{",
        f"if (!truthy({result}.done)) {{",
    }:
        return None
    value_end = _find_block_end(body, value_if)
    if value_end is None or value_end != len(body) - 1:
        return None
    value_body = body[value_if + 1 : value_end]
    if len(value_body) != 2:
        return None
    store = re.match(rf"^(r\d+)\[(r\d+)\] = {re.escape(result)}\.value$", value_body[0])
    if not store or value_body[1] != f"{store.group(2)} += 1":
        return None
    return store.group(1), store.group(2)

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
            next_store = re.match(
                rf"^(r\d+) = {re.escape(iter_reg)}\.next$",
                lines[cursor + 1].strip(),
            )
            if not next_store:
                continue
            next_reg = next_store.group(1)
            cursor += 2

        while_idx = None
        for candidate in range(cursor, min(cursor + 10, len(lines))):
            if lines[candidate].strip() == "while (!(truthy(ACCU))) {":
                while_idx = candidate
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
            lines, while_idx, while_end, iter_reg, next_reg, flag_reg
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
) -> Optional[Tuple[List[str], set[str], str]]:
    body_start = while_idx + 1
    result_reg: Optional[str] = None
    call_index: Optional[int] = None
    expected_call = (
        f"{next_reg}.call({iter_reg})"
        if next_reg is not None
        else f"{iter_reg}.next()"
    )
    for index in range(body_start, min(body_start + 12, while_end)):
        match = re.match(r"^(r\d+) = (.+)$", lines[index].strip())
        if match and match.group(2) == expected_call:
            result_reg = match.group(1)
            call_index = index
            break
    if result_reg is None or call_index is None:
        return None

    cursor = call_index + 1
    if cursor < while_end and lines[cursor].strip() == "if (!(isJSReceiver(ACCU))) {":
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
    if lines[cursor].strip() == f"ACCU = {result_reg}.done":
        cursor += 1
        if cursor >= while_end or lines[cursor].strip() not in {
            "if (!truthy(ACCU)) {",
            "if (!(truthy(ACCU))) {",
        }:
            return None
    elif lines[cursor].strip() not in {
        f"if (!truthy({result_reg}.done)) {{",
        f"if (!(truthy({result_reg}.done))) {{",
    }:
        return None
    value_block_end = _find_block_end(lines, cursor)
    if value_block_end is None or value_block_end > while_end:
        return None

    value_body = [item.strip() for item in lines[cursor + 1 : value_block_end]]
    try:
        value_store = value_body.index(f"{result_reg} = {result_reg}.value")
    except ValueError:
        return None
    body_cursor = value_store + 1
    if body_cursor < len(value_body) and value_body[body_cursor] == "ACCU = false":
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
    for index in range(close_end + 1, min(close_end + 16, len(lines))):
        guard = re.match(
            r"^if \((r\d+|-?\d+) === 0\) \{$", lines[index].strip()
        )
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
