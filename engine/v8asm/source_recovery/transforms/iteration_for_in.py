"""For-in loop recovery transforms."""

from __future__ import annotations

import re
from typing import List

from .common import _extract_indent, _find_block_end


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
