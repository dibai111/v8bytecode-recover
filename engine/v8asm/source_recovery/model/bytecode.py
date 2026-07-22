"""Bytecode-array and decoded-line models."""

from typing import Optional
import re

from .base import *


class CodeLine:
    def __init__(self, offset: int, bytestr: str, mnemonic: str, operands: str, raw: str):
        self.offset = offset          # 指令偏移（如 0）
        self.bytestr = bytestr        # 原始字节串（如 "0b 04"）
        self.mnemonic = mnemonic      # 助记符（如 "Ldar"）
        self.operands = operands      # 操作数（如 "a1"）
        self.raw = raw                # 原始整行文本

    @classmethod
    def from_text(cls, line: str) -> "CodeLine":
        # 例: "0x18336d7c0110 @    0 : 0b 04             Ldar a1"
        try:
            after_at = line.split("@", 1)[1].strip()
            offset_str, rest = after_at.split(":", 1)
            offset = int(offset_str.strip())
            rest = rest.strip()
            # 字节码 bytes 部分和指令分隔：助记符一般是第一个非 hex token
            parts = rest.split()
            # 先取 hex 部分
            bytes_list = []
            hex_token = re.compile(r"^[0-9a-f]{2}$")
            while parts and hex_token.match(parts[0]):
                bytes_list.append(parts.pop(0))
            bytestr = " ".join(bytes_list)
            mnemonic = parts[0] if parts else ""
            operands = " ".join(parts[1:]) if len(parts) > 1 else ""
            return cls(offset, bytestr, mnemonic, operands, line)
        except Exception:
            return cls(-1, "", "", "", line)

    def __repr__(self):
        return f"<CodeLine offset={self.offset} {self.mnemonic} {self.operands}>"


class HandlerEntry:
    def __init__(self, start: int, end: int, handler: int, prediction: int, data: int):
        self.start = start
        self.end = end
        self.handler = handler
        self.prediction = prediction
        self.data = data

    @classmethod
    def from_text(cls, line: str) -> "HandlerEntry":
        # example: (  19,  65)  ->    71 (prediction=0, data=10)
        m = re.match(
            r"\(\s*(\d+),\s*(\d+)\)\s*->\s*(\d+)\s*\(prediction=(\d+),\s*data=(\d+)\)",
            line.strip()
        )
        if m:
            return cls(
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3)),
                int(m.group(4)),
                int(m.group(5)),
            )
        return cls(-1, -1, -1, -1, -1)

    def __repr__(self):
        return f"<HandlerEntry from={self.start} to={self.end} -> {self.handler} pred={self.prediction} data={self.data}>"


class V8BytecodeArray(V8HeapObject):
    def __init__(self, address: int, i_type: str, lines: List[str]):
        super().__init__(address, i_type, lines)
        self.parameter_count: Optional[int] = None
        self.register_count: Optional[int] = None
        self.frame_size: Optional[int] = None
        self.constant_pool_size: Optional[int] = None
        self.handler_table_size: Optional[int] = None
        self.source_position_table_size: Optional[int] = None
        self.instructions: List[CodeLine] = []
        self.handler_entries: List[HandlerEntry] = []

    def parse(self):
        in_handler_table = False
        for ln in self.raw_asm_lines:
            s = ln.strip()
            if s.startswith("Parameter count"):
                self.parameter_count = int(s.split()[-1])
            elif s.startswith("Register count"):
                self.register_count = int(s.split()[-1])
            elif s.startswith("Frame size"):
                self.frame_size = int(s.split()[-1])
            elif "Constant pool (size =" in s:
                self.constant_pool_size = int(s.split("=")[1].split(")")[0])
                in_handler_table = False
            elif "Handler Table (size =" in s:
                self.handler_table_size = int(s.split("=")[1].split(")")[0])
                in_handler_table = True
            elif "Source Position Table (size =" in s:
                self.source_position_table_size = int(s.split("=")[1].split(")")[0])
                in_handler_table = False
            elif "@" in s and "0x" in s:
                # Some V8 builds prepend source-position markers like:
                # "111 S> 0x... @   19 : ...". Keep only the instruction tail.
                start = s.find("0x")
                ins = s[start:] if start >= 0 else s
                self.instructions.append(CodeLine.from_text(ins))
            elif in_handler_table and s.startswith("("):
                entry = HandlerEntry.from_text(s)
                self.handler_entries.append(entry)
            else:
                pass
        self._normalize_handler_offsets()

    def _normalize_handler_offsets(self):
        """Reconcile handler-table PCs with disassembly instruction offsets.

        Some V8 dump profiles expose handler PCs in two-byte code units while
        printing instruction offsets in bytes.  Select the scale from handler
        entry evidence instead of tying the parser to a V8 version.
        """
        if not self.handler_entries or not self.instructions:
            return

        def score(scale: int) -> int:
            total = 0
            for entry in self.handler_entries:
                target = entry.handler * scale
                candidates = [
                    (line.offset, index)
                    for index, line in enumerate(self.instructions)
                    if target <= line.offset <= target + 4
                ]
                if not candidates:
                    continue
                offset, index = candidates[0]
                total += 1
                if offset == target:
                    total += 1
                window = [
                    line.mnemonic
                    for line in self.instructions[index : index + 8]
                    if line.offset <= target + 32
                ]
                if any(name.startswith("CreateCatchContext") for name in window):
                    total += 8
                if "SetPendingMessage" in window:
                    total += 4
                if any(name in {"ReThrow", "Throw"} for name in window):
                    total += 2
            return total

        direct_score = score(1)
        doubled_score = score(2)
        if doubled_score <= direct_score:
            scale = 1
        else:
            scale = 2
        for entry in self.handler_entries:
            entry.handler *= scale
            if entry.prediction == 0:
                continue
            for index, instruction in enumerate(self.instructions[:-1]):
                if instruction.offset < entry.handler:
                    continue
                if instruction.offset > entry.handler + 4:
                    break
                following = self.instructions[index + 1]
                if (
                    instruction.mnemonic.startswith("Star")
                    and following.mnemonic.startswith("CreateCatchContext")
                ):
                    entry.handler = instruction.offset
                    break

    def __repr__(self):
        return (f"<V8BytecodeArray: 0x{self.address:012x} "
                f"params={self.parameter_count} regs={self.register_count} "
                f"frame={self.frame_size} instrs={len(self.instructions)} "
                f"const_pool_size={self.constant_pool_size} "
                f"handler_table_size={self.handler_table_size} "
                f"sptable_size={self.source_position_table_size}>")
