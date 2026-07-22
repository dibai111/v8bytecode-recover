"""Opt-in machine-readable analysis views for decoded bytecode."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .analysis.cfg import (
    TERMINATORS,
    build_basic_blocks,
    is_conditional,
    is_loop_jump,
    is_unconditional_jump,
)
from .analysis.instruction import Instruction
from .analysis.utils import parse_jump_target
from .model import V8BytecodeArray
from .parsing import parse_objects


def _successors(block, blocks) -> list[int]:
    term = block.terminator
    if term is None:
        return []
    starts = [candidate.start for candidate in blocks]
    try:
        position = starts.index(block.start)
    except ValueError:
        return []
    fallthrough = starts[position + 1] if position + 1 < len(starts) else None
    target = parse_jump_target(term)
    result: list[int] = []
    if is_conditional(term.mnemonic):
        if target in starts:
            result.append(target)
        if fallthrough is not None:
            result.append(fallthrough)
    elif is_unconditional_jump(term.mnemonic) or is_loop_jump(term.mnemonic):
        if target in starts:
            result.append(target)
    elif term.mnemonic not in TERMINATORS and fallthrough is not None:
        result.append(fallthrough)
    return list(dict.fromkeys(result))


def control_flow_document(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    objects = parse_objects(lines)
    functions = []
    for obj in objects:
        if not isinstance(obj, V8BytecodeArray):
            continue
        instructions = [Instruction.from_codeline(line) for line in obj.instructions]
        blocks = build_basic_blocks(instructions)
        functions.append(
            {
                "bytecode_address": f"0x{obj.address:012x}",
                "parameter_count": obj.parameter_count,
                "register_count": obj.register_count,
                "handler_count": len(obj.handler_entries),
                "blocks": [
                    {
                        "start": block.start,
                        "end": block.end,
                        "successors": _successors(block, blocks),
                        "instructions": [
                            {
                                "offset": instruction.offset,
                                "mnemonic": instruction.mnemonic,
                                "operands": instruction.args,
                            }
                            for instruction in block.instructions
                        ],
                    }
                    for block in blocks
                ],
            }
        )
    return {"format": 1, "functions": functions}


def main() -> int:
    parser = argparse.ArgumentParser(prog="source_recovery.research")
    parser.add_argument("input", type=Path)
    parser.add_argument("--emit", choices=("cfg",), required=True)
    args = parser.parse_args()
    print(json.dumps(control_flow_document(args.input), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
