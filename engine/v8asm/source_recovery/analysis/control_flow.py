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
        # Maps a loop's exit offset to the break condition while that loop is
        # being emitted; blocks terminating at the offset render in-place
        # `if (condition) break` so bottom-tested loops keep their test order.
        self.active_loop_exits: Dict[int, str] = {}
        # Compare/load instructions consumed by a top-tested loop header; the
        # body emitter skips them so the condition is not duplicated.
        self.suppressed_instruction_offsets: Set[int] = set()
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
            # Bottom-tested loops (exit branch mid-body, before JumpLoop) must
            # keep their exit test in place: hoisting it to a while-header
            # reads the tested operands after the body redefined them. Only
            # such loops register in-place exits; top-tested loops keep the
            # classic while-header shape.
            exit_offsets = self._loop_exit_offsets(loop_region)
            bottom_tested = self._is_bottom_tested_loop(loop_region)
            condition = "true"
            consumed_offsets: Set[int] = set()
            if bottom_tested:
                for exit_offset in exit_offsets:
                    self.active_loop_exits[exit_offset] = (
                        self._loop_exit_condition(loop_region)
                    )
            else:
                condition, consumed = self._top_test_condition(
                    loop_region
                )
                consumed_offsets = consumed
                self.suppressed_instruction_offsets |= consumed_offsets
                # The exit branch itself is now expressed by the header; the
                # inner emission of the head block must not re-render it as an
                # if/break.
                self.suppressed_instruction_offsets |= set(exit_offsets)
                consumed_offsets |= set(exit_offsets)
            try:
                body, _ = self._emit_region(loop_region.start, loop_region.end)
            finally:
                for exit_offset in exit_offsets:
                    self.active_loop_exits.pop(exit_offset, None)
                self.suppressed_instruction_offsets -= consumed_offsets
            self.active_loops.remove(block.start)
            loop_stmt = LoopStatement(
                condition=condition, body=body, bottom_tested=bottom_tested
            )
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
            compare_chain = self._build_compare_chain_switch(block_idx, stop_offset)
            if compare_chain is not None:
                return compare_chain
            terminal_switch = self._build_terminal_switch(block_idx, stop_offset)
            if terminal_switch is not None:
                return terminal_switch

        for instr in body_instrs:
            if instr.offset in self.suppressed_instruction_offsets:
                continue
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

        if is_conditional(term.mnemonic) and term.offset in self.active_loop_exits:
            # Bottom-tested loop exit: the branch leaves the loop, so render
            # the test exactly where V8 evaluated it.
            condition = self.active_loop_exits[term.offset]
            info = self._resolved_branch_condition(term, block)
            if info:
                expr, branch_on_true = info
                condition = expr if branch_on_true else f"!({expr})"
            statements.append(
                IfStatement(
                    condition=condition,
                    then_branch=[SimpleStatement("break")],
                )
            )
            return statements, block_idx + 1

        if is_conditional(term.mnemonic) and term.offset in self.suppressed_instruction_offsets:
            # The branch is a top-tested loop exit already expressed in the
            # loop header; continuing to the next block is unconditional.
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

        # Resume after the LAST case/default region. ordered_targets[-1] is
        # itself a case target; resuming there would re-emit its body as bare
        # statements. Each case region already ends with a terminator (break /
        # return / throw), so continue past every dispatched block.
        last_target_idx = max(
            idx
            for idx in (
                self.offset_to_index.get(target)
                for target in ordered_targets
            )
            if idx is not None
        )
        resume = last_target_idx + 1
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

    def _compare_chain_case(
        self, block: BasicBlock
    ) -> Optional[Tuple[str, str, int, int]]:
        """Parse a compare-chain case block into (value, compared_expr, target, head).

        V8 lowers small switches to a chain of blocks shaped ``load constant;
        ACCU = (subject === ACCU); [Mov shuffles]; JumpIfTrue case_body``.
        Mov shuffles may appear between the compare and the branch (and before
        the constant load); everything before the constant load is real prefix
        code. Returns head = index of the load so the caller can treat the
        leading instructions as prefix. Returns None for shapes that are not a
        switch-case test (plain ifs fall back to _build_if).
        """
        instrs = block.instructions
        term = instrs[-1] if instrs else None
        if term is None or term.mnemonic not in {"JumpIfTrue", "JumpIfTrueConstant"}:
            return None
        target = parse_jump_target(term)
        if target is None:
            return None

        pos = len(instrs) - 2
        while pos >= 0 and instrs[pos].mnemonic == "Mov":
            pos -= 1
        if pos < 0:
            return None
        comparison = self.translator.translate(instrs[pos]).strip()
        comparison_match = re.fullmatch(
            r"ACCU\s*=\s*\((.+?)\s*===\s*ACCU\)", comparison
        )
        if not comparison_match:
            return None

        pos -= 1
        while pos >= 0 and instrs[pos].mnemonic == "Mov":
            pos -= 1
        if pos < 0:
            return None
        load = self.translator.translate(instrs[pos]).strip()
        load_match = re.fullmatch(r"ACCU\s*=\s*(.+)", load)
        if not load_match:
            return None
        value = load_match.group(1).strip()
        if not re.fullmatch(
            r'-?(?:\d+(?:\.\d+)?|0x[0-9a-fA-F]+)|true|false|null|undefined|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
            value,
        ):
            return None
        return value, comparison_match.group(1).strip(), target, pos

    @staticmethod
    def _is_register_copy(text: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z_$][\w$]*\s*=\s*[A-Za-z_$][\w$]*", text))

    def _mov_aliases(self, block: BasicBlock) -> Dict[str, str]:
        """Register aliasing introduced by Mov inside a block (dest -> src)."""
        aliases: Dict[str, str] = {}
        for instr in block.instructions[:-1]:
            if instr.mnemonic != "Mov" or len(instr.args) < 2:
                continue
            src = self.translator._reg_name(instr.args[0])
            dest = self.translator._reg_name(instr.args[1])
            if dest == src or not re.fullmatch(r"[A-Za-z_$][\w$]*", dest):
                continue
            aliases[dest] = src
        return aliases

    def _build_compare_chain_switch(
        self, block_idx: int, stop_offset: Optional[int]
    ) -> Optional[Tuple[List[Statement], int]]:
        """Recover a compare-chain switch diamond as a switch statement.

        Shape (per case i): a block ending in ``load constant value;
        TestEqualStrict subj_i; JumpIfTrue body_i`` where each body ends with
        an unconditional jump to a shared exit, and the final chain block
        jumps to the shared default instead of testing again. V8 inserts Mov
        shuffles between tests, so subjects are unified through register
        aliases collected from the whole chain before comparing them.
        Without this matcher the generic if-builder drops the default body
        after the chain, where it runs unconditionally - a semantic corruption.
        """
        raw_cases: List[Tuple[str, str, int]] = []
        alias_union: Dict[str, str] = {}
        default_target: Optional[int] = None
        prefix_instrs: List[Instruction] = []
        index = block_idx

        while index < len(self.blocks):
            block = self.blocks[index]
            term = block.terminator
            if term is None:
                return None
            if is_unconditional_jump(term.mnemonic):
                default_target = parse_jump_target(term)
                break
            parsed = self._compare_chain_case(block)
            if parsed is None:
                return None
            value, compared, target, head = parsed
            if index == block_idx:
                # Instructions before the first test are real code the switch
                # must preserve (e.g. computing the subject).
                prefix_instrs = list(block.instructions[:head])
            else:
                for instr in block.instructions[:head]:
                    text = self.translator.translate(instr).strip()
                    if text and text != "ACCU = ACCU" and not self._is_register_copy(text):
                        return None
            raw_cases.append((value, compared, target))
            alias_union.update(self._mov_aliases(block))
            index += 1
        else:
            return None

        def canonical(expr: str) -> str:
            seen: Set[str] = set()
            while expr in alias_union and expr not in seen:
                seen.add(expr)
                expr = alias_union[expr]
            return expr

        subjects = {canonical(compared) for _, compared, _ in raw_cases}
        # A single case plus default still forms a switch; the >= 2 gate only
        # guards against mistaking plain if/else for a chain.
        if (
            not raw_cases
            or default_target is None
            or len(subjects) != 1
            or (len(raw_cases) < 2 and default_target <= block.start)
        ):
            return None
        if not raw_cases:
            return None
        subject = next(iter(subjects))
        cases = [
            (value, target) for value, _, target in raw_cases
        ]

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
        if any(idx is None for idx in target_indices.values()):
            return None

        # Case/default regions end at the next dispatch boundary; the shared
        # exit offset is what every case body's trailing Jump aims at.
        case_exit: Optional[int] = None
        for target in sorted(targets - {default_target}):
            term = self.blocks[self.offset_to_index[target]].terminator
            if term and is_unconditional_jump(term.mnemonic):
                candidate = parse_jump_target(term)
                if candidate is not None:
                    case_exit = candidate
                    break

        ordered_targets = sorted(targets)
        bodies: Dict[int, List[Statement]] = {}
        for position, target in enumerate(ordered_targets):
            boundary = (
                ordered_targets[position + 1]
                if position + 1 < len(ordered_targets)
                else (case_exit if case_exit is not None else stop_offset)
            )
            body, _ = self._emit_region(target, boundary)
            if target != default_target:
                # A trailing jump to another case target is a deliberate
                # fallthrough from the original source; anything jumping to
                # the shared exit becomes `break`.
                last = getattr(body[-1], "text", "").strip() if body else ""
                fallthrough = parse_jump_target(
                    self.blocks[self.offset_to_index[target]].terminator
                )
                if not (last.startswith("goto ") and fallthrough in targets
                        and fallthrough != case_exit):
                    strip_trailing_goto(body, case_exit if case_exit is not None else -1)
                    self._ensure_case_break(body)
            bodies[target] = body

        grouped: Dict[int, List[str]] = {}
        for value, target in cases:
            grouped.setdefault(target, []).append(value)
        switch_cases = [
            SwitchCase(values=values, body=bodies[target])
            for target, values in sorted(grouped.items())
        ]
        prefix = [
            SimpleStatement(text)
            for text in (
                self.translator.translate(instr).strip()
                for instr in prefix_instrs
            )
            if text and text.strip() != "ACCU = ACCU"
        ]
        prefix.append(
            SwitchStatement(
                subject=subject,
                cases=switch_cases,
                default_branch=bodies[default_target],
            )
        )

        # Resume at the shared exit: every case/default region ends there, so
        # continuing from the chain would re-emit bodies as bare statements.
        if case_exit is None or case_exit not in self.offset_to_index:
            return prefix, len(self.blocks)
        return prefix, self.offset_to_index[case_exit]

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
            if instr.offset in self.suppressed_instruction_offsets:
                continue
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

    def _resolved_branch_condition(
        self, term, block: BasicBlock
    ) -> Optional[Tuple[str, bool]]:
        """Branch condition with a generic ACCU test resolved to its operand.

        JumpIfToBooleanTrue-style terminators translate to `truthy(ACCU)`; when
        the tested value was produced by an instruction inside the same block,
        substitute it. A compare feeding the branch (for example
        `TestLessThanOrEqual` producing `ACCU = (r1 <= ACCU)`) is resolved one
        step further by replacing its own trailing ACCU with the operand loaded
        before it, so loop headers read `(r1 <= arg0)` instead of a bare
        truthiness test.
        """
        info = self.translator.branch_condition(term)
        if not info or "ACCU" not in info[0]:
            return info
        expr, branch_on_true = info
        instructions = block.instructions[:-1]
        for position in range(len(instructions) - 1, -1, -1):
            instr = instructions[position]
            text = self.translator.translate(instr).strip()
            load = re.match(r"^ACCU\s*=\s*(.+)$", text)
            if not load:
                break
            value = load.group(1).strip()
            if "ACCU" in value:
                # Compare chain: resolve the inner ACCU from the next-earlier
                # ACCU load, then use the completed expression.
                for inner_position in range(position - 1, -1, -1):
                    inner_text = self.translator.translate(
                        instructions[inner_position]
                    ).strip()
                    inner_load = re.match(r"^ACCU\s*=\s*(.+)$", inner_text)
                    if not inner_load:
                        break
                    inner_value = inner_load.group(1).strip()
                    if "ACCU" in inner_value:
                        break
                    value = re.sub(
                        r"\bACCU\b", lambda _match: inner_value, value
                    )
                    return (
                        re.sub(r"\bACCU\b", lambda _match: value, expr),
                        branch_on_true,
                    )
                break
            return re.sub(r"\bACCU\b", lambda _match: value, expr), branch_on_true
        return info

    def _loop_exit_offsets(self, region: LoopRegion) -> List[int]:
        """Instruction offsets of branches that leave the loop to its end."""
        start_idx = self.offset_to_index.get(region.start, 0)
        end_idx = self.offset_to_index.get(region.end, len(self.blocks))
        offsets: List[int] = []
        for idx in range(start_idx, end_idx):
            term = self.blocks[idx].terminator
            if not term:
                continue
            target = parse_jump_target(term)
            if target == region.end and term.mnemonic.startswith("JumpIf"):
                offsets.append(term.offset)
        return offsets

    def _is_bottom_tested_loop(self, region: LoopRegion) -> bool:
        """True when the exit test runs after body work (V8 iterator loops).

        A classic while-loop re-tests at the top: the exit branch terminates
        the block that starts at region.start. A bottom-tested loop evaluates
        its condition mid-region — the exit block begins after body work such
        as an iterator .next() call, so hoisting the test would read operands
        before or after the body redefines them.
        """
        start_idx = self.offset_to_index.get(region.start, 0)
        end_idx = self.offset_to_index.get(region.end, len(self.blocks))
        for idx in range(start_idx, end_idx):
            block = self.blocks[idx]
            term = block.terminator
            if not term:
                continue
            target = parse_jump_target(term)
            if target == region.end and term.mnemonic.startswith("JumpIf"):
                return block.start != region.start
        return False

    def _top_test_condition(
        self, region: LoopRegion
    ) -> Tuple[str, Set[int]]:
        """Header condition for a top-tested loop, plus consumed offsets.

        The head block ends with the exit branch; the compare feeding it (and
        the operand load immediately before it) belong to the header, not the
        body. Resolve `ACCU` in the branch condition against that compare so
        classic loops recover as `while ((x <= n))` instead of an opaque
        `truthy(ACCU)` test.
        """
        start_idx = self.offset_to_index.get(region.start, 0)
        end_idx = self.offset_to_index.get(region.end, len(self.blocks))
        for idx in range(start_idx, end_idx):
            block = self.blocks[idx]
            term = block.terminator
            if not term or term.offset < 0:
                continue
            target = parse_jump_target(term)
            if target != region.end or not term.mnemonic.startswith("JumpIf"):
                continue
            info = self._resolved_branch_condition(term, block)
            if not info:
                return "true", set()
            expr, branch_on_true = info
            condition = f"!({expr})" if branch_on_true else expr
            consumed: Set[int] = set()
            # Consume every trailing ACCU-producing instruction of the head
            # block: the compare and its operand loads now live only in the
            # resolved header expression, so emitting them in the body would
            # duplicate the test.
            body = block.instructions[:-1]
            for instr in reversed(body):
                text = self.translator.translate(instr).strip()
                if not re.match(r"^ACCU\s*=\s*", text):
                    break
                consumed.add(instr.offset)
                if len(consumed) >= 4:
                    break
            return condition, consumed
        return "true", set()

    def _loop_exit_condition(self, region: LoopRegion) -> Optional[str]:
        """Condition under which the loop's exit branch leaves."""
        start_idx = self.offset_to_index.get(region.start, 0)
        end_idx = self.offset_to_index.get(region.end, len(self.blocks))
        for idx in range(start_idx, end_idx):
            block = self.blocks[idx]
            term = block.terminator
            if not term:
                continue
            target = parse_jump_target(term)
            if target == region.end and term.mnemonic.startswith("JumpIf"):
                info = self._resolved_branch_condition(term, block)
                if info:
                    expr, branch_on_true = info
                    return expr if branch_on_true else f"!({expr})"
        return None

    def _loop_exit_condition_negated(self, region: LoopRegion) -> str:
        """Continue-condition for a top-tested loop whose exit branch leaves.

        Uses the raw branch_condition (no ACCU substitution): for a top-tested
        loop the compare lives inside the emitted body, and the JS-side
        condition-load inliner resolves `ACCU` against it after lowering.
        Substituting here would fold the condition to stale initial values.
        """
        start_idx = self.offset_to_index.get(region.start, 0)
        end_idx = self.offset_to_index.get(region.end, len(self.blocks))
        for idx in range(start_idx, end_idx):
            term = self.blocks[idx].terminator
            if not term:
                continue
            target = parse_jump_target(term)
            if target == region.end and term.mnemonic.startswith("JumpIf"):
                info = self.translator.branch_condition(term)
                if info:
                    expr, branch_on_true = info
                    # Continue while the exit branch is NOT taken.
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
