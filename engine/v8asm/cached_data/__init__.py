"""Runtime-independent V8 cached-data decoding."""

from .decoder import disassemble_bytes, disassemble_file

__all__ = ["disassemble_bytes", "disassemble_file"]
