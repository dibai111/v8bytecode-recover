import unittest

try:
    from .analysis.instruction import Instruction
    from .analysis.translator import InstructionTranslator
    from .context import DecompilerContext
    from .core import _prepare_generator_instructions
    from .transforms.pipeline import simplify_lines
    from .model import V8Address, V8ArrayBoilerplateDescription
    from .model.bytecode import V8BytecodeArray
    from .transforms.high_level import _normalize_block_indentation
except ImportError:  # unittest discover with source_recovery as the top-level path.
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from source_recovery.analysis.instruction import Instruction  # type: ignore[no-redef]
    from source_recovery.analysis.translator import InstructionTranslator  # type: ignore[no-redef]
    from source_recovery.context import DecompilerContext  # type: ignore[no-redef]
    from source_recovery.core import _prepare_generator_instructions  # type: ignore[no-redef]
    from source_recovery.transforms.pipeline import simplify_lines  # type: ignore[no-redef]
    from source_recovery.model import (  # type: ignore[no-redef]
        V8Address,
        V8ArrayBoilerplateDescription,
    )
    from source_recovery.model.bytecode import V8BytecodeArray  # type: ignore[no-redef]
    from source_recovery.transforms.high_level import (  # type: ignore[no-redef]
        _normalize_block_indentation,
    )


class SourceRecoveryTests(unittest.TestCase):
    def test_switch_case_bodies_keep_one_extra_indent(self):
        lines = [
            "function serialize(value) {",
            "switch (value) {",
            'case "one":',
            "return 1",
            "default:",
            "return 0",
            "}",
            "}",
        ]

        self.assertEqual(
            _normalize_block_indentation(lines),
            [
                "function serialize(value) {",
                "  switch (value) {",
                '    case "one":',
                "      return 1",
                "    default:",
                "      return 0",
                "  }",
                "}",
            ],
        )

    def test_legacy_comparison_opcodes_translate_to_javascript(self):
        bytecode = V8BytecodeArray(0x100, "BytecodeArray", [])
        translator = InstructionTranslator(DecompilerContext([]), bytecode)

        self.assertEqual(
            translator.translate(Instruction(0, "TestNull", [], "TestNull")),
            "ACCU = (ACCU === null)",
        )
        self.assertEqual(
            translator.translate(Instruction(1, "ToNumber", [], "ToNumber")),
            "ACCU = Number(ACCU)",
        )

    def test_legacy_arithmetic_and_bitwise_opcodes_translate_to_javascript(self):
        bytecode = V8BytecodeArray(0x100, "BytecodeArray", [])
        translator = InstructionTranslator(DecompilerContext([]), bytecode)

        cases = [
            (("Exp", ["r2", "[4]"]), "ACCU = (r2 ** ACCU)"),
            (("BitwiseXor", ["r2", "[4]"]), "ACCU = (r2 ^ ACCU)"),
            (("BitwiseXorSmi", ["[7]", "[4]"]), "ACCU = (ACCU ^ 7)"),
            (("ShiftRightLogical", ["r2", "[4]"]), "ACCU = (r2 >>> ACCU)"),
            (("ShiftRightLogicalSmi", ["[7]", "[4]"]), "ACCU = (ACCU >>> 7)"),
            (("Negate", ["[4]"]), "ACCU = -ACCU"),
            (("Debugger", []), ""),
        ]
        for (mnemonic, args), expected in cases:
            instruction = Instruction(0, mnemonic, args, mnemonic)
            self.assertEqual(translator.translate(instruction), expected, mnemonic)

    def test_empty_legacy_double_array_boilerplate_formats_as_an_array(self):
        boilerplate = V8ArrayBoilerplateDescription(
            0x100,
            "ArrayBoilerplateDescription",
            [],
        )
        boilerplate.elements_kind = "4"
        boilerplate.constant_elements = V8Address(
            0x101,
            "<fixed_double_array_map>",
        )

        context = DecompilerContext([boilerplate])

        self.assertEqual(context._format_array_boilerplate(boilerplate), "[]")

    def test_regular_generator_is_detected_and_yields_values(self):
        bytecode = V8BytecodeArray(
            0x100,
            "BytecodeArray",
            [
                "0x00000100 @ 0 : SwitchOnGeneratorState r0, [0], [2]",
                "0x00000104 @ 4 : InvokeIntrinsic [CreateJSGeneratorObject], r1-r2",
                "0x00000108 @ 8 : SuspendGenerator r0, r0-r1, [0]",
                "0x0000010c @ 12 : ResumeGenerator r0, r0-r1",
                "0x00000110 @ 16 : InvokeIntrinsic [GeneratorGetResumeMode], r0-r0",
                "0x00000114 @ 20 : SwitchOnSmiNoFeedback [2], [2], [0]",
                "0x00000118 @ 24 : InvokeIntrinsic [CreateIterResultObject], r2-r3",
                "0x0000011c @ 28 : Return",
            ],
        )
        bytecode.parse()
        translator = InstructionTranslator(DecompilerContext([]), bytecode)
        prepared = _prepare_generator_instructions(
            translator,
            [Instruction.from_codeline(line) for line in bytecode.instructions],
        )

        self.assertTrue(translator.is_generator)
        self.assertFalse(translator.is_async_generator)
        self.assertFalse(any(item.mnemonic == "SwitchOnGeneratorState" for item in prepared))
        self.assertFalse(any(
            item.mnemonic == "InvokeIntrinsic"
            and item.args
            and item.args[0].strip("[]") == "CreateJSGeneratorObject"
            for item in prepared
        ))
        self.assertEqual(
            translator.translate(
                Instruction(
                    0,
                    "InvokeIntrinsic",
                    ["[CreateIterResultObject]", "r2-r3"],
                    "CreateIterResultObject",
                )
            ),
            "ACCU = yield r2",
        )

    def test_suspend_results_are_not_inlined_as_a_second_yield(self):
        self.assertEqual(
            simplify_lines([
                "ACCU = yield r2",
                "r2 = ACCU",
            ]),
            [
                "ACCU = yield r2",
                "r2 = ACCU",
            ],
        )

    def test_async_function_state_machine_is_not_misclassified_as_a_generator(self):
        bytecode = V8BytecodeArray(
            0x200,
            "BytecodeArray",
            [
                "0x00000200 @ 0 : SwitchOnGeneratorState r0, [0], [2]",
                "0x00000204 @ 4 : InvokeIntrinsic [AsyncFunctionAwaitUncaught], r0-r1",
            ],
        )
        bytecode.parse()
        translator = InstructionTranslator(DecompilerContext([]), bytecode)

        self.assertTrue(translator.is_async_function)
        self.assertFalse(translator.is_generator)


if __name__ == "__main__":
    unittest.main()
