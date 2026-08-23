"""Structured statement model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


INDENT = "  "


class Statement:
    def render(self, indent: int = 0) -> List[str]:
        raise NotImplementedError


@dataclass
class SimpleStatement(Statement):
    text: str

    def render(self, indent: int = 0) -> List[str]:
        if not self.text:
            return []
        return [f"{INDENT * indent}{self.text}"]


@dataclass
class RawLinesStatement(Statement):
    lines: List[str] = field(default_factory=list)

    def render(self, indent: int = 0) -> List[str]:
        prefix = INDENT * indent
        return [f"{prefix}{line}" if line else "" for line in self.lines]


@dataclass
class IfStatement(Statement):
    condition: str
    then_branch: List[Statement] = field(default_factory=list)
    else_branch: Optional[List[Statement]] = None

    def render(self, indent: int = 0) -> List[str]:
        lines = [f"{INDENT * indent}if ({self.condition}) {{"]
        for stmt in self.then_branch:
            lines.extend(stmt.render(indent + 1))
        lines.append(f"{INDENT * indent}}}")
        if self.else_branch:
            lines.append(f"{INDENT * indent}else {{")
            for stmt in self.else_branch:
                lines.extend(stmt.render(indent + 1))
            lines.append(f"{INDENT * indent}}}")
        return lines


@dataclass
class LoopStatement(Statement):
    condition: str
    body: List[Statement] = field(default_factory=list)
    # Bottom-tested loops (V8 emits the exit branch mid-body, before JumpLoop)
    # keep that exit test in place as a trailing `if (cond) break`. Hoisting it
    # into a while-header reads the tested operands after the body redefined
    # them. Top-tested loops keep the classic `while (cond)` shape so every
    # downstream cleanup pass still recognizes it.
    bottom_tested: bool = False

    def render(self, indent: int = 0) -> List[str]:
        if not self.bottom_tested or self.condition == "true":
            lines = [f"{INDENT * indent}while ({self.condition}) {{"]
            for stmt in self.body:
                lines.extend(stmt.render(indent + 1))
            lines.append(f"{INDENT * indent}}}")
            return lines
        lines = [f"{INDENT * indent}while (true) {{"]
        for stmt in self.body:
            lines.extend(stmt.render(indent + 1))
        lines.append(f"{INDENT * (indent + 1)}if ({self.condition}) {{")
        lines.append(f"{INDENT * (indent + 2)}break")
        lines.append(f"{INDENT * (indent + 1)}}}")
        lines.append(f"{INDENT * indent}}}")
        return lines


@dataclass
class SwitchCase:
    values: List[str] = field(default_factory=list)
    body: List[Statement] = field(default_factory=list)


@dataclass
class SwitchStatement(Statement):
    subject: str
    cases: List[SwitchCase] = field(default_factory=list)
    default_branch: List[Statement] = field(default_factory=list)

    def render(self, indent: int = 0) -> List[str]:
        lines = [f"{INDENT * indent}switch ({self.subject}) {{"]
        for case in self.cases:
            for value in case.values:
                lines.append(f"{INDENT * (indent + 1)}case {value}:")
            for stmt in case.body:
                lines.extend(stmt.render(indent + 2))
        if self.default_branch or not self.cases:
            lines.append(f"{INDENT * (indent + 1)}default:")
            for stmt in self.default_branch:
                lines.extend(stmt.render(indent + 2))
        lines.append(f"{INDENT * indent}}}")
        return lines
