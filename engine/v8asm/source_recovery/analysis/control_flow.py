"""Recover structured statements from the control-flow graph."""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from .cfg import (
    BasicBlock,
    LoopRegion,
    build_basic_blocks,
    find_loop_regions,
    is_conditional,
    is_loop_jump,
    is_unconditional_jump,
    TERMINATORS,
)
from .statements import (
    IfStatement,
    LoopStatement,
    RawLinesStatement,
    SimpleStatement,
    Statement,
)
from .translator import InstructionTranslator
from .utils import parse_jump_target, strip_trailing_goto


class Structurer:
    def __init__(
        self,
        translator: InstructionTranslator,
        blocks: List[BasicBlock],
        recovered_regions: Optional[Dict[int, List[str]]] = None,
    ):
        self.translator = translator
        self.blocks = blocks
        self.recovered_regions = recovered_regions or {}
        self.offset_to_index: Dict[int, int] = {
            block.start: idx for idx, block in enumerate(blocks)
        }
        self.loop_regions = find_loop_regions(blocks)
        self.active_loops: Set[int] = set()
        self.active_if_builds: Set[int] = set()
        self.pending_raw_branch_targets: Set[int] = set()

    def build(self) -> List[Statement]:
        if not self.blocks:
            return []
        start = self.blocks[0].start
        statements, _ = self._emit_region(start, None)
        return statements

    def _emit_region(
        self, start_offset: int, stop_offset: Optional[int]
    ) -> Tuple[List[Statement], int]:
        idx = self.offset_to_index.get(start_offset, 0)
        statements: List[Statement] = []
        seen_indices: Set[int] = set()
        while idx < len(self.blocks):
            if idx in seen_indices:
                break
            seen_indices.add(idx)
            block = self.blocks[idx]
            if stop_offset is not None and block.start >= stop_offset:
                break
            current_idx = idx
            produced, idx = self._emit_block(idx, stop_offset)
            if idx in seen_indices:
                idx += 1
            statements.extend(produced)
            terminator = self.blocks[current_idx].terminator
            if terminator and terminator.mnemonic in TERMINATORS:
                break
        return statements, idx

    def _emit_block(
        self, block_idx: int, stop_offset: Optional[int]
    ) -> Tuple[List[Statement], int]:
        block = self.blocks[block_idx]
        statements: List[Statement] = []
        self.pending_raw_branch_targets.discard(block.start)

        if stop_offset is not None and block.start >= stop_offset:
            return statements, block_idx

        loop_region = self.loop_regions.get(block.start)
        if loop_region and block.start not in self.active_loops:
            self.active_loops.add(block.start)
            body, _ = self._emit_region(loop_region.start, loop_region.end)
            self.active_loops.remove(block.start)
            condition = self._loop_condition(loop_region)
            loop_stmt = LoopStatement(condition=condition, body=body)
            next_idx = self.offset_to_index.get(loop_region.end, len(self.blocks))
            return [loop_stmt], next_idx

        instructions = block.instructions[:]
        if not instructions:
            return statements, block_idx + 1

        term = instructions[-1]
        body_instrs = instructions[:-1] if len(instructions) > 1 else []
        for instr in body_instrs:
            recovered = self.recovered_regions.get(instr.offset)
            if instr.mnemonic == "RecoveredTryCatch" and recovered is not None:
                statements.append(RawLinesStatement(recovered))
                continue
            text = self.translator.translate(instr)
            if text:
                statements.append(SimpleStatement(text))

        if term is None:
            return statements, block_idx + 1

        recovered = self.recovered_regions.get(term.offset)
        if term.mnemonic == "RecoveredTryCatch" and recovered is not None:
            statements.append(RawLinesStatement(recovered))
            return statements, block_idx + 1

        if is_conditional(term.mnemonic):
            target = parse_jump_target(term)
            if not self._is_pending_raw_dispatch_target(block.start, target):
                short_circuit = self._build_shared_short_circuit_if(
                    block_idx, stop_offset
                )
                if short_circuit:
                    stmt, next_idx = short_circuit
                    statements.append(stmt)
                    return statements, next_idx
                built = self._build_if(block_idx, stop_offset)
                if built:
                    stmt, next_idx = built
                    statements.append(stmt)
                    return statements, next_idx

        if term.mnemonic == "Return":
            statements.append(SimpleStatement(self.translator.translate(term)))
            return statements, block_idx + 1

        if is_loop_jump(term.mnemonic):
            # Closing jump of a loop – skip explicit goto.
            return statements, block_idx + 1

        if is_unconditional_jump(term.mnemonic):
            target = parse_jump_target(term)
            if target is not None:
                statements.append(SimpleStatement(f"goto offset_{target}"))
                if self._has_pending_raw_target_between(block.start, target):
                    self.pending_raw_branch_targets.add(target)
                    return statements, block_idx + 1
                next_idx = self.offset_to_index.get(target, block_idx + 1)
                return statements, next_idx

        if term.mnemonic.startswith("JumpIf"):
            # Fallback when structure reconstruction failed.
            statements.append(SimpleStatement(self.translator.translate(term)))
            target = parse_jump_target(term)
            if target is not None and target > block.start:
                self.pending_raw_branch_targets.add(target)
            return statements, block_idx + 1

        text = self.translator.translate(term)
        if text:
            statements.append(SimpleStatement(text))
        return statements, block_idx + 1

    def _build_shared_short_circuit_if(
        self, block_idx: int, stop_offset: Optional[int]
    ) -> Optional[Tuple[Statement, int]]:
        if block_idx + 2 >= len(self.blocks):
            return None
        first = self.blocks[block_idx]
        second = self.blocks[block_idx + 1]
        first_term = first.terminator
        second_term = second.terminator
        if (
            first_term is None
            or second_term is None
            or not is_conditional(first_term.mnemonic)
            or not is_conditional(second_term.mnemonic)
        ):
            return None

        common_start = parse_jump_target(first_term)
        alternate_start = parse_jump_target(second_term)
        if (
            common_start is None
            or alternate_start is None
            or common_start <= first.start
            or alternate_start <= common_start
            or self.blocks[block_idx + 2].start != common_start
        ):
            return None

        common_idx = self.offset_to_index.get(common_start)
        alternate_idx = self.offset_to_index.get(alternate_start)
        if common_idx is None or alternate_idx is None or common_idx >= alternate_idx:
            return None

        joins: List[int] = []
        for index in range(common_idx, alternate_idx):
            candidate_term = self.blocks[index].terminator
            if candidate_term and is_unconditional_jump(candidate_term.mnemonic):
                candidate = parse_jump_target(candidate_term)
                if candidate is not None and candidate > alternate_start:
                    joins.append(candidate)
        if not joins:
            return None
        join_offset = min(joins)
        if stop_offset is not None and join_offset >= stop_offset:
            return None

        first_info = self.translator.branch_condition(first_term)
        second_condition = self.translator.fallthrough_condition(second_term)
        if not first_info or not second_condition:
            return None
        first_expr, first_branch_on_true = first_info
        first_condition = first_expr if first_branch_on_true else f"!({first_expr})"

        common_statements, _ = self._emit_region(common_start, alternate_start)
        strip_trailing_goto(common_statements, join_offset)
        alternate_statements, _ = self._emit_region(alternate_start, join_offset)
        strip_trailing_goto(alternate_statements, join_offset)

        second_prefix: List[Statement] = []
        for instr in second.instructions[:-1]:
            recovered = self.recovered_regions.get(instr.offset)
            if instr.mnemonic == "RecoveredTryCatch" and recovered is not None:
                second_prefix.append(RawLinesStatement(recovered))
                continue
            text = self.translator.translate(instr)
            if text:
                second_prefix.append(SimpleStatement(text))
        second_prefix.append(
            IfStatement(
                condition=second_condition,
                then_branch=list(common_statements),
                else_branch=alternate_statements or None,
            )
        )
        return (
            IfStatement(
                condition=first_condition,
                then_branch=common_statements,
                else_branch=second_prefix,
            ),
            self.offset_to_index.get(join_offset, len(self.blocks)),
        )

    def _loop_condition(self, region: LoopRegion) -> str:
        start_idx = self.offset_to_index.get(region.start, 0)
        end_idx = self.offset_to_index.get(region.end, len(self.blocks))
        for idx in range(start_idx, end_idx):
            block = self.blocks[idx]
            term = block.terminator
            if not term:
                continue
            target = parse_jump_target(term)
            if target == region.end and term.mnemonic.startswith("JumpIf"):
                info = self.translator.branch_condition(term)
                if info:
                    expr, branch_on_true = info
                    return f"!({expr})" if branch_on_true else expr
        return "true"

    def _build_if(
        self, block_idx: int, stop_offset: Optional[int]
    ) -> Optional[Tuple[Statement, int]]:
        guard_key = block_idx
        if guard_key in self.active_if_builds:
            return None
        self.active_if_builds.add(guard_key)
        try:
            return self._build_if_inner(block_idx, stop_offset)
        finally:
            self.active_if_builds.remove(guard_key)

    def _build_if_inner(
        self, block_idx: int, stop_offset: Optional[int]
    ) -> Optional[Tuple[Statement, int]]:
        block = self.blocks[block_idx]
        term = block.terminator
        if term is None:
            return None
        target = parse_jump_target(term)
        if target is None or target <= block.start:
            return None

        condition = self.translator.fallthrough_condition(term)
        if not condition:
            return None

        fallthrough_idx = block_idx + 1
        if fallthrough_idx >= len(self.blocks):
            return None
        fallthrough_start = self.blocks[fallthrough_idx].start

        then_statements, _ = self._emit_region(fallthrough_start, target)
        strip_trailing_goto(then_statements, target)
        else_statements: Optional[List[Statement]] = None
        join_offset = target

        target_idx = self.offset_to_index.get(target)
        if target_idx is not None:
            target_term = self.blocks[target_idx].terminator
            if target_term and target_term.mnemonic in TERMINATORS:
                continuations = []
                for index in range(fallthrough_idx, target_idx):
                    candidate = parse_jump_target(self.blocks[index].terminator)
                    if candidate is not None and candidate > target:
                        continuations.append(candidate)
                if continuations:
                    join_offset = min(continuations)
                    else_statements, _ = self._emit_region(target, join_offset)
            else:
                continuations = [
                    candidate
                    for index in range(fallthrough_idx, target_idx)
                    if (candidate := parse_jump_target(self.blocks[index].terminator))
                    is not None
                    and candidate > target
                ]
                unique_continuations = set(continuations)
                if len(unique_continuations) == 1:
                    candidate = next(iter(unique_continuations))
                    candidate_idx = self.offset_to_index.get(candidate)
                    target_region_is_bounded = (
                        candidate_idx is not None
                        and candidate_idx > target_idx
                        and all(
                            (branch_target := parse_jump_target(
                                self.blocks[index].terminator
                            )) is None
                            or target <= branch_target <= candidate
                            for index in range(target_idx, candidate_idx)
                        )
                    )
                    if target_region_is_bounded:
                        join_offset = candidate
                        else_statements, _ = self._emit_region(
                            target, join_offset
                        )

        if else_statements is not None:
            strip_trailing_goto(then_statements, join_offset)
            strip_trailing_goto(else_statements, join_offset)

        last_idx = self._block_index_before(target)
        if else_statements is None and last_idx is not None:
            last_block = self.blocks[last_idx]
            last_term = last_block.terminator
            if last_term and is_unconditional_jump(last_term.mnemonic):
                join_candidate = parse_jump_target(last_term)
                if join_candidate and join_candidate > target:
                    join_offset = join_candidate
                    else_statements, _ = self._emit_region(target, join_offset)
                    strip_trailing_goto(then_statements, join_offset)
                    if else_statements:
                        strip_trailing_goto(else_statements, join_offset)

        next_idx = self.offset_to_index.get(join_offset, len(self.blocks))
        stmt = IfStatement(
            condition=condition,
            then_branch=then_statements,
            else_branch=else_statements,
        )
        return stmt, next_idx

    def _block_index_before(self, offset: int) -> Optional[int]:
        result = None
        for idx, block in enumerate(self.blocks):
            if block.start < offset:
                result = idx
            else:
                break
        return result

    def _has_pending_raw_target_between(self, start: int, end: int) -> bool:
        if end <= start:
            return False
        return any(start < target < end for target in self.pending_raw_branch_targets)

    def _is_pending_raw_dispatch_target(
        self, start: int, target: Optional[int]
    ) -> bool:
        if target is None or target <= start or not self.pending_raw_branch_targets:
            return False
        first_pending = min(self.pending_raw_branch_targets)
        return target >= first_pending


def decompile_to_statements(
    translator: InstructionTranslator,
    instructions: List[Instruction],
    recovered_regions: Optional[Dict[int, List[str]]] = None,
) -> List[Statement]:
    blocks = build_basic_blocks(instructions)
    structurer = Structurer(translator, blocks, recovered_regions)
    return structurer.build()
