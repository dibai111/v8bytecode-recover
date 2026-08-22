"""Recover structured statements from the control-flow graph."""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

from .cfg import (
    BasicBlock,
    LoopRegion,
    build_basic_blocks,
    find_loop_regions,
    is_conditional,
    is_loop_jump,
    is_unconditional_jump,
    is_switch_dispatch,
    TERMINATORS,
)
from .statements import (
    IfStatement,
    LoopStatement,
    RawLinesStatement,
    SimpleStatement,
    Statement,
    SwitchCase,
    SwitchStatement,
)
from .translator import InstructionTranslator
from .utils import parse_jump_target, strip_trailing_goto

SWITCH_TABLE_MNEMONIC = "SwitchOnSmiNoFeedback"
_CASE_BODY_STOPPERS = ("return ", "throw ", "break", "continue", "goto offset_")


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

    @staticmethod
    def _block_terminates(block: BasicBlock) -> bool:
        terminator = block.terminator
        if terminator is None:
            return False
        return terminator.mnemonic in TERMINATORS or (
            terminator.mnemonic == "RecoveredTryCatch"
            and "terminal" in terminator.args
        )

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
            if self._block_terminates(self.blocks[current_idx]):
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

        smi_switch = self._build_smi_switch(block_idx, stop_offset)
        if smi_switch is not None:
            return smi_switch

        if is_conditional(term.mnemonic):
            terminal_switch = self._build_terminal_switch(block_idx, stop_offset)
            if terminal_switch is not None:
                return terminal_switch

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

    def _smi_switch_table(
        self, instr
    ) -> Optional[List[Tuple[int, int]]]:
        """Resolve a SwitchOnSmiNoFeedback jump table into (value, target) pairs."""
        if instr.mnemonic != SWITCH_TABLE_MNEMONIC or len(instr.args) < 3:
            return None
        numbers: List[int] = []
        for token in instr.args[:3]:
            match = re.fullmatch(r"\[(-?\d+)\]", token.strip())
            if not match:
                return None
            numbers.append(int(match.group(1)))
        table_index, table_size, first_case = numbers
        if table_size <= 0:
            return None
        entries: List[Tuple[int, int]] = []
        for position in range(table_size):
            entry = self.translator.constants.get(table_index + position)
            if entry is None:
                return None
            display = entry.display.strip()
            if not re.fullmatch(r"-?\d+", display):
                return None
            target = instr.offset + int(display)
            if target <= instr.offset:
                return None
            entries.append((first_case + position, target))
        return entries

    def smi_switch_jump_targets(
        self, instructions: List[Instruction]
    ) -> Set[int]:
        """Collect every case-target offset reachable through a jump table."""
        targets: Set[int] = set()
        index_by_offset = {
            instr.offset: idx for idx, instr in enumerate(instructions)
        }
        for instr in instructions:
            if instr.mnemonic != SWITCH_TABLE_MNEMONIC:
                continue
            entries = self._smi_switch_table(instr)
            if not entries:
                continue
            for _, target in entries:
                if target in index_by_offset:
                    targets.add(target)
        return targets

    def _build_smi_switch(
        self, block_idx: int, stop_offset: Optional[int]
    ) -> Optional[Tuple[List[Statement], int]]:
        """Recover an integer jump-table dispatch as a switch statement.

        Case targets were registered as basic-block leaders, so each case body
        occupies its own block range; the fallthrough after the dispatch is the
        default branch. Every target must fall inside the current region so
        bodies are emitted without crossing enclosing structure boundaries.
        """
        block = self.blocks[block_idx]
        term = block.terminator
        if term is None or not is_switch_dispatch(term.mnemonic):
            return None
        entries = self._smi_switch_table(term)
        if not entries:
            return None

        subject = self._switch_subject(block)
        if subject is None:
            return None

        targets = sorted({target for _, target in entries})
        if any(target >= stop_offset for target in targets) if stop_offset is not None else False:
            return None
        target_indices = {
            target: self.offset_to_index.get(target) for target in targets
        }
        if any(idx is None for idx in target_indices.values()):
            return None

        default_target = self.blocks[block_idx + 1].start
        if stop_offset is not None and default_target >= stop_offset:
            return None
        ordered_targets = sorted(set(targets) | {default_target})
        boundary_by_target = {
            target: (
                ordered_targets[i + 1] if i + 1 < len(ordered_targets) else stop_offset
            )
            for i, target in enumerate(ordered_targets)
        }
        grouped: Dict[int, List[str]] = {}
        for value, target in entries:
            grouped.setdefault(target, []).append(str(value))

        cases: List[SwitchCase] = []
        for target in ordered_targets:
            body, _ = self._emit_region(target, boundary_by_target[target])
            strip_trailing_goto(body, target)
            if target != default_target:
                self._ensure_case_break(body)
            cases.append(SwitchCase(values=grouped.get(target, []), body=body))

        named_cases = [case for case in cases if case.values]
        default_branch = next(
            (case.body for case in cases if not case.values), None
        )
        prefix = [
            SimpleStatement(text)
            for text in (
                self.translator.translate(instr).strip()
                for instr in block.instructions[:-1]
            )
            if text
        ]
        prefix.append(
            SwitchStatement(
                subject=subject,
                cases=named_cases,
                default_branch=default_branch,
            )
        )

        resume = self.offset_to_index.get(
            ordered_targets[-1], len(self.blocks)
        )
        return prefix, len(self.blocks) if resume == block_idx + 1 else resume

    @staticmethod
    def _ensure_case_break(body: List[Statement]) -> None:
        for statement in reversed(body):
            text = getattr(statement, "text", None)
            if not isinstance(text, str):
                continue
            stripped = text.strip()
            if stripped.startswith(_CASE_BODY_STOPPERS):
                return
            break
        body.append(SimpleStatement("break"))

    def _switch_subject(self, block: BasicBlock) -> Optional[str]:
        for instr in reversed(block.instructions[:-1]):
            text = self.translator.translate(instr).strip()
            match = re.fullmatch(r"ACCU\s*=\s*(.+)", text)
            if match:
                expr = match.group(1).strip()
                if "ACCU" in expr:
                    return None
                return expr
            # Star-style stores keep the value alive; keep scanning upward.
            if re.fullmatch(r"r\d+\s*=\s*ACCU", text) or text.startswith("//"):
                continue
            return None
        return None

    def _build_terminal_switch(
        self, block_idx: int, stop_offset: Optional[int]
    ) -> Optional[Tuple[List[Statement], int]]:
        cases: List[Tuple[str, int]] = []
        subject: Optional[str] = None
        prefix: List[Statement] = []
        index = block_idx

        while index < len(self.blocks):
            block = self.blocks[index]
            term = block.terminator
            if term is None:
                return None
            if is_unconditional_jump(term.mnemonic):
                default_target = parse_jump_target(term)
                default_prefix = self._translate_instructions(block.instructions[:-1])
                break
            if not is_conditional(term.mnemonic):
                return None

            parsed = self._switch_case(block)
            target = parse_jump_target(term)
            if parsed is None or target is None:
                return None
            case_value, case_subject, setup = parsed
            if subject is None:
                subject = case_subject
                prefix = self._translate_instructions(setup)
            elif case_subject != subject or self._translate_instructions(setup):
                return None
            cases.append((case_value, target))
            index += 1
        else:
            return None

        if subject is None or len(cases) < 2 or default_target is None:
            return None
        chain_end = self.blocks[index].start
        targets = {target for _, target in cases}
        targets.add(default_target)
        if len(targets) < 2 or any(target <= chain_end for target in targets):
            return None
        if stop_offset is not None and any(target >= stop_offset for target in targets):
            return None

        target_indices = {
            target: self.offset_to_index.get(target) for target in targets
        }
        if any(target_index is None for target_index in target_indices.values()):
            return None
        if any(
            not self._block_terminates(self.blocks[target_index])
            for target_index in target_indices.values()
            if target_index is not None
        ):
            return None

        ordered_targets = sorted(targets)
        target_bodies: Dict[int, List[Statement]] = {}
        for position, target in enumerate(ordered_targets):
            boundary = (
                ordered_targets[position + 1]
                if position + 1 < len(ordered_targets)
                else stop_offset
            )
            target_bodies[target], _ = self._emit_region(target, boundary)

        grouped: Dict[int, List[str]] = {}
        for value, target in cases:
            grouped.setdefault(target, []).append(value)
        switch_cases = [
            SwitchCase(values=values, body=target_bodies[target])
            for target, values in grouped.items()
        ]
        default_branch = [*default_prefix, *target_bodies[default_target]]
        prefix.append(
            SwitchStatement(
                subject=subject,
                cases=switch_cases,
                default_branch=default_branch,
            )
        )
        return prefix, len(self.blocks)

    def _switch_case(
        self, block: BasicBlock
    ) -> Optional[Tuple[str, str, List[Instruction]]]:
        if len(block.instructions) < 3:
            return None
        term = block.instructions[-1]
        condition = self.translator.branch_condition(term)
        if condition not in {("truthy(ACCU)", True), ("ACCU", True)}:
            return None

        case_load = self.translator.translate(block.instructions[-3]).strip()
        comparison = self.translator.translate(block.instructions[-2]).strip()
        value_match = re.fullmatch(r"ACCU\s*=\s*(.+)", case_load)
        comparison_match = re.fullmatch(
            r"ACCU\s*=\s*\((.+?)\s*(?:===|==)\s*ACCU\)", comparison
        )
        if not value_match or not comparison_match:
            return None
        value = value_match.group(1).strip()
        if not re.fullmatch(
            r'-?(?:\d+(?:\.\d+)?|0x[0-9a-fA-F]+)|true|false|null|undefined|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
            value,
        ):
            return None
        return value, comparison_match.group(1).strip(), block.instructions[:-3]

    def _translate_instructions(
        self, instructions: List[Instruction]
    ) -> List[Statement]:
        statements: List[Statement] = []
        for instr in instructions:
            recovered = self.recovered_regions.get(instr.offset)
            if instr.mnemonic == "RecoveredTryCatch" and recovered is not None:
                statements.append(RawLinesStatement(recovered))
                continue
            text = self.translator.translate(instr)
            if text:
                statements.append(SimpleStatement(text))
        return statements

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
            if self._block_terminates(self.blocks[target_idx]):
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
    probe = Structurer(translator, build_basic_blocks(instructions), recovered_regions)
    blocks = build_basic_blocks(instructions, extra_leaders=probe.smi_switch_jump_targets(instructions))
    structurer = Structurer(translator, blocks, recovered_regions)
    return structurer.build()
