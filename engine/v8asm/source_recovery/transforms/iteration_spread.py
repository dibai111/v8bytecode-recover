"""Array spread lowering from V8 iterator bytecode."""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .common import _extract_indent, _find_block_end


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
