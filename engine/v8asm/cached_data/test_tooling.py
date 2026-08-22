from __future__ import annotations

import unittest
import struct
import tempfile
from pathlib import Path
from textwrap import dedent

try:
    from .tooling.generate_profiles import (
        parse_bytecodes,
        parse_operand_types,
        parse_scope_info_layout,
    )
    from .tooling.discover_versions import select_tags
    from .tooling.source_layout import SourceLayout, SourceLayoutError
    from .profile_cli import coverage_report
    from .profiles import load_profiles
except ImportError:  # unittest discover with cached_data as the top-level path.
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from cached_data.tooling.generate_profiles import (  # type: ignore[no-redef]
        parse_bytecodes,
        parse_operand_types,
        parse_scope_info_layout,
    )
    from cached_data.tooling.discover_versions import select_tags  # type: ignore[no-redef]
    from cached_data.tooling.source_layout import (  # type: ignore[no-redef]
        SourceLayout,
        SourceLayoutError,
    )
    from cached_data.profile_cli import coverage_report  # type: ignore[no-redef]
    from cached_data.profiles import load_profiles  # type: ignore[no-redef]


class _Reader:
    def __init__(self, files: dict[str, str]):
        self.files = files

    def read_optional(self, version: str, path: str) -> str | None:
        return self.files.get(path)


class ProfileToolingTests(unittest.TestCase):
    def test_selects_latest_exact_tag_per_minor_line(self):
        tags = ["5.1.281", "5.1.282", "5.2.0", "15.3.25", "15.3.79"]

        self.assertEqual(
            select_tags(tags, (5, 1, 0), None, per_minor=True, limit=None),
            ["5.1.282", "5.2.0", "15.3.79"],
        )

    def test_can_select_all_exact_tags_with_bounds(self):
        tags = ["5.1.281", "5.1.282", "5.2.0", "15.3.25", "15.3.79"]

        self.assertEqual(
            select_tags(tags, (5, 1, 281), (5, 2, 0), per_minor=False, limit=None),
            ["5.1.281", "5.1.282", "5.2.0"],
        )

    def test_normalizes_operand_widths_from_source(self):
        source = dedent(
            """
        #define OPERAND_TYPE_LIST(V) \\
          V(MaybeReg, OperandTypeInfo::kScalableSignedByte) \\
          V(ConstantPoolIndex, OperandTypeInfo::kScalableUnsignedByte) \\
          V(AbortReason, OperandTypeInfo::kFixedUnsignedByte) \\
          V(EmbeddedFeedback, OperandTypeInfo::kFixedUnsignedShort)
            """
        )

        self.assertEqual(
            parse_operand_types(source),
            {
                "MaybeReg": "Reg",
                "ConstantPoolIndex": "Idx",
                "AbortReason": "Flag8",
                "EmbeddedFeedback": "Flag16",
            },
        )

    def test_uses_source_operand_contract_when_parsing_bytecodes(self):
        source = dedent(
            """
        #define BYTECODE_LIST(V) \\
          V(Lda, AccumulatorUse::kNone, OperandType::kEmbeddedFeedback) \\
          V(Call, AccumulatorUse::kRead, OperandType::kMaybeReg)
            """
        )
        operands = parse_operand_types(
            dedent(
                """
            V(EmbeddedFeedback, OperandTypeInfo::kFixedUnsignedByte)
            V(MaybeReg, OperandTypeInfo::kScalableSignedByte)
                """
            )
        )

        bytecodes = parse_bytecodes(source, operands)

        self.assertEqual(bytecodes[0]["operands"], ["Flag8"])
        self.assertEqual(bytecodes[1]["operands"], ["Reg"])

    def test_detects_legacy_and_unknown_source_layouts(self):
        legacy = SourceLayout.detect(
            _Reader({"src/snapshot/serializer-common.h": "legacy"}), "5.1.281"
        )
        self.assertEqual(legacy.name, "legacy-cpp")
        self.assertEqual(
            legacy.read(_Reader({"src/interpreter/bytecodes.h": "ops"}), "5.1.281", "bytecode_operands"),
            "ops",
        )

        with self.assertRaises(SourceLayoutError) as context:
            SourceLayout.detect(_Reader({}), "99.0.0")
        self.assertEqual(context.exception.layout, "unknown")
        self.assertIn("serializer tags", str(context.exception))

    def test_detects_serializer_h_as_a_legacy_layout(self):
        layout = SourceLayout.detect(
            _Reader({"src/snapshot/serializer.h": "historical"}), "5.0.0"
        )

        self.assertEqual(layout.name, "legacy-cpp")

    def test_legacy_layout_keeps_torque_scope_flags_and_cpp_header(self):
        reader = _Reader(
            {
                "src/snapshot/serializer-common.h": "legacy",
                "src/objects/scope-info.tq": "torque flags",
                "src/objects/scope-info.h": "fixed-array header",
            }
        )
        layout = SourceLayout.detect(reader, "8.1.307.31")

        self.assertEqual(layout.name, "legacy-cpp")
        self.assertEqual(layout.read(reader, "8.1.307.31", "scope_info"), "torque flags")
        self.assertEqual(
            layout.read_optional(reader, "8.1.307.31", "scope_info_header"),
            "fixed-array header",
        )

    def test_parses_hybrid_torque_and_fixed_array_scope_info(self):
        torque = dedent(
            """
            extern class ScopeInfo extends FixedArray;
            bitfield struct ScopeFlags extends uint32 {
              scope_type: ScopeType: 4 bit;
              has_saved_class_variable_index: bool: 1 bit;
              function_variable: VariableAllocationInfo: 2 bit;
              has_inferred_function_name: bool: 1 bit;
            }
            """
        )
        header = dedent(
            """
            #define FOR_EACH_SCOPE_INFO_NUMERIC_FIELD(V) \\
              V(Flags) \\
              V(ParameterCount) \\
              V(ContextLocalCount)
            """
        )

        layout = parse_scope_info_layout(
            torque,
            "enum ScopeType { CLASS_SCOPE, EVAL_SCOPE, FUNCTION_SCOPE, MODULE_SCOPE };",
            header,
        )

        self.assertEqual(layout["flags_encoding"], "smi")
        self.assertEqual(layout["variable_part_slot"], 5)
        self.assertEqual(layout["flags_slot"], 2)
        self.assertEqual(layout["context_count_slot"], 4)
        self.assertEqual(layout["saved_class_variable_bit"], 4)
        self.assertEqual(layout["function_variable_shift"], 5)
        self.assertEqual(layout["inferred_function_name_bit"], 7)

    def test_parses_qualified_legacy_scope_bit_fields(self):
        source = dedent(
            """
            #define FOR_EACH_SCOPE_INFO_NUMERIC_FIELD(V) \\
              V(Flags) \\
              V(ParameterCount) \\
              V(ContextLocalCount)
            // Properties of scopes.
            using ScopeTypeBits = base::BitField<ScopeType, 0, 4>;
            using FunctionVariableBits = ScopeTypeBits::Next<VariableAllocationInfo, 2>;
            using HasSavedClassVariableIndexBit = FunctionVariableBits::Next<bool, 1>;
            using HasInferredFunctionNameBit = HasSavedClassVariableIndexBit::Next<bool, 1>;
            // Properties of variables.
            """
        )

        layout = parse_scope_info_layout(source, "")

        self.assertEqual(layout["scope_type_shift"], 0)
        self.assertEqual(layout["function_variable_shift"], 4)
        self.assertEqual(layout["saved_class_variable_bit"], 6)
        self.assertEqual(layout["inferred_function_name_bit"], 7)

    def test_reports_exact_profile_coverage_for_a_local_blob(self):
        profile = load_profiles().by_version("9.4.146.24")
        data = bytearray(28)
        struct.pack_into("<I", data, 0, 0xC0DE0001)
        struct.pack_into("<I", data, 4, profile.version_hash)
        struct.pack_into("<I", data, 16, 4)
        struct.pack_into("<I", data, 20, 0xAABBCCDD)
        struct.pack_into("<I", data, 24, 0xDEADBEEF)
        with tempfile.TemporaryDirectory() as directory:
            blob = Path(directory) / "sample.jsc"
            blob.write_bytes(data)
            report = coverage_report(directory)

        self.assertEqual(report["matched"], 1)
        self.assertEqual(report["failed"], 0)
        self.assertEqual(report["profiles"]["9.4.146.24"], 1)

    def test_empty_coverage_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            report = coverage_report(directory)

        self.assertTrue(report["empty"])
        self.assertEqual(report["file_count"], 0)


if __name__ == "__main__":
    unittest.main()
