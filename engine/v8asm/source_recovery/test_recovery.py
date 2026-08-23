import unittest

try:
    from .analysis.instruction import Instruction
    from .analysis.translator import InstructionTranslator
    from .context import DecompilerContext
    from .core import _prepare_generator_instructions, render_level4
    from .transforms.pipeline import simplify_lines
    from .model import V8Address, V8ArrayBoilerplateDescription
    from .model.bytecode import V8BytecodeArray
    from .model.arrays import V8TrustedFixedArray
    from .transforms.high_level import _normalize_block_indentation
except ImportError:  # unittest discover with source_recovery as the top-level path.
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from source_recovery.analysis.instruction import Instruction  # type: ignore[no-redef]
    from source_recovery.analysis.translator import InstructionTranslator  # type: ignore[no-redef]
    from source_recovery.context import DecompilerContext  # type: ignore[no-redef]
    from source_recovery.core import (  # type: ignore[no-redef]
        _prepare_generator_instructions,
        render_level4,
    )
    from source_recovery.transforms.pipeline import simplify_lines  # type: ignore[no-redef]
    from source_recovery.model import (  # type: ignore[no-redef]
        V8Address,
        V8ArrayBoilerplateDescription,
    )
    from source_recovery.model.bytecode import V8BytecodeArray  # type: ignore[no-redef]
    from source_recovery.model.arrays import V8TrustedFixedArray  # type: ignore[no-redef]
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

    def test_switch_on_smi_jump_table_recovers_a_switch_statement(self):
        bytecode = V8BytecodeArray(
            0x300,
            "BytecodeArray",
            [
                "0x00000300 <BytecodeArray 0x300> (constant_pool_size = 7)",
                "Parameter count 2",
                "Constant pool (size = 7)",
                "Register count 4",
                "Frame size 32",
                "0x00000300 @ 0 : Ldar a0",
                "0x00000304 @ 4 : SwitchOnSmiNoFeedback [4], [3], [1]",
                "0x00000308 @ 8 : LdaSmi [40]",
                "0x0000030c @ 12 : Return",
                "0x00000310 @ 16 : LdaSmi [41]",
                "0x00000314 @ 20 : Return",
                "0x00000318 @ 24 : LdaSmi [42]",
                "0x0000031c @ 28 : Return",
                "0x00000320 @ 32 : LdaZero",
                "0x00000324 @ 36 : Return",
            ],
        )
        bytecode.parse()
        instructions = [
            Instruction.from_codeline(line) for line in bytecode.instructions
        ]
        # Table starts at pool index 4 ([4] operand); deltas are relative to the
        # dispatch at offset 4: cases 1/2/3 land on offsets 16/20/24, and the
        # fallthrough (offset 8) is the default branch.
        pool = V8TrustedFixedArray(
            bytecode.address + 1,
            "TrustedFixedArray",
            [
                "- length: 7",
                "0: 0",
                "1: 0",
                "2: 0",
                "3: 0",
                "4: 12",
                "5: 16",
                "6: 20",
            ],
        )
        pool.parse()
        ctx = DecompilerContext([bytecode, pool])
        self.assertIn(bytecode.address, ctx.bytecode_constant_pools)
        translator = InstructionTranslator(ctx, bytecode)

        lines = render_level4(ctx, bytecode, translator, instructions)

        joined = "\n".join(lines)
        self.assertIn("switch (", joined)
        self.assertIn("case 1:", joined)
        self.assertIn("case 2:", joined)
        self.assertIn("case 3:", joined)
        self.assertLess(joined.index("case 1:"), joined.index("case 2:"))
        self.assertNotIn("SwitchOnSmiNoFeedback", joined)

    def test_compare_chain_switch_recovers_with_default_inside_structure(self):
        bytecode = V8BytecodeArray(
            0x400,
            "BytecodeArray",
            [
                # Real V8 shape for a small switch: compare chain with Mov
                # shuffles, per-case bodies jumping to a shared exit, and the
                # default body reached by the final unconditional Jump.
                "0x00000400 @ 0 : Ldar a0",
                "0x00000402 @ 2 : MulSmi [2], [0]",
                "0x00000405 @ 5 : Star0",
                "0x00000406 @ 6 : LdaSmi [6]",
                "0x00000408 @ 8 : TestEqualStrict r0, [1]",
                "0x0000040b @ 11 : Mov r0, r1",
                "0x0000040e @ 14 : JumpIfTrue [11] (@ 25)",
                "0x00000410 @ 16 : LdaSmi [8]",
                "0x00000412 @ 18 : TestEqualStrict r1, [1]",
                "0x00000415 @ 21 : JumpIfTrue [12] (@ 33)",
                "0x00000417 @ 23 : Jump [18] (@ 41)",
                "0x00000419 @ 25 : Ldar r0",
                "0x0000041b @ 27 : MulSmi [10], [2]",
                "0x0000041e @ 30 : Star0",
                "0x00000420 @ 31 : Jump [13] (@ 44)",
                "0x00000422 @ 33 : Ldar r0",
                "0x00000424 @ 35 : SubSmi [1], [3]",
                "0x00000427 @ 38 : Star0",
                "0x00000429 @ 39 : Jump [5] (@ 44)",
                "0x0000042b @ 41 : LdaSmi [-1]",
                "0x0000042d @ 43 : Star0",
                "0x0000042f @ 44 : Ldar r0",
                "0x00000431 @ 46 : Return",
            ],
        )
        bytecode.parse()
        instructions = [
            Instruction.from_codeline(line) for line in bytecode.instructions
        ]
        translator = InstructionTranslator(DecompilerContext([]), bytecode)

        lines = render_level4(
            DecompilerContext([]), bytecode, translator, instructions
        )

        joined = "\n".join(lines)
        self.assertIn("switch (", joined)
        self.assertIn("case 6:", joined)
        self.assertIn("case 8:", joined)
        self.assertIn("default:", joined)
        # The default body must live inside the switch, not leak past it.
        default_pos = joined.index("default:")
        close_pos = joined.index("}", default_pos)
        self.assertIn("= -1", joined[default_pos:close_pos])
        self.assertNotIn("-1", joined[close_pos:])
        # Case bodies need explicit breaks or the first case falls through.
        self.assertIn("break", joined)

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
