"""Intermediate representation boundary for source recovery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .cfg import BasicBlock, build_basic_blocks
from .instruction import Instruction


@dataclass(frozen=True)
class InstructionIR:
    instruction: Instruction


@dataclass(frozen=True)
class FunctionIR:
    instructions: tuple[InstructionIR, ...]
    blocks: tuple[BasicBlock, ...]


def build_function_ir(instructions: List[Instruction]) -> FunctionIR:
    """Build the stable instruction and basic-block boundary used by renderers."""
    blocks = build_basic_blocks(instructions)
    return FunctionIR(
        instructions=tuple(InstructionIR(item) for item in instructions),
        blocks=tuple(blocks),
    )
