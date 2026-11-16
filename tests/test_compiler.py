import unittest

from mapping_compiler import (
    compile_mapping,
    ConfigError,
    CycleError,
    DuplicateTargetError,
    ExecutionError,
    IntegerOverflowError,
    TypeMismatchError,
    UnknownFieldError,
)


class TestRenameAndConvert(unittest.TestCase):
    def test_simple_rename(self):
        mapping = compile_mapping(
            {"name": "str"},
            [{"target": "user_name", "source": "name", "type": "str"}],
        )
        self.assertEqual(mapping.apply({"name": "alice"}), {"user_name": "alice"})

    def test_explicit_type_conversion(self):
        mapping = compile_mapping(
            {"age_text": "str"},
            [
                {
                    "target": "age",
                    "expr": "to_int(age_text)",
                    "type": "int32",
                }
            ],
        )
        self.assertEqual(mapping.apply({"age_text": " 42 "}), {"age": 42})

    def test_int_widens_to_float(self):
        mapping = compile_mapping(
            {"score": "int32"},
            [{"target": "score_f", "source": "score", "type": "float"}],
        )
        self.assertEqual(mapping.apply({"score": 7}), {"score_f": 7.0})


class TestChainedDependencies(unittest.TestCase):
    def test_forward_dependency_resolved_by_topo_sort(self):
        # 配置顺序与依赖顺序相反：c 依赖 b，b 依赖 a
        mapping = compile_mapping(
            {"price": "float", "qty": "int32"},
            [
                {"target": "c_total", "expr": "b_taxed * qty", "type": "float"},
                {"target": "b_taxed", "expr": "a_net * 1.13", "type": "float"},
                {"target": "a_net", "expr": "price * 0.9", "type": "float"},
            ],
        )
        self.assertEqual(
            mapping.execution_order, ["a_net", "b_taxed", "c_total"]
        )
        result = mapping.apply({"price": 100.0, "qty": 2})
        self.assertAlmostEqual(result["a_net"], 90.0)
        self.assertAlmostEqual(result["b_taxed"], 101.7)
        self.assertAlmostEqual(result["c_total"], 203.4)

    def test_dependency_through_condition(self):
        mapping = compile_mapping(
            {"level": "int32"},
            [
                {
                    "target": "bonus",
                    "expr": "base * 2",
                    "type": "int32",
                    "when": "level > 3",
                    "default": 0,
                },
                {"target": "base", "expr": "level * 10", "type": "int32"},
            ],
        )
        self.assertEqual(mapping.execution_order, ["base", "bonus"])
        self.assertEqual(
            mapping.apply({"level": 5}),
            {"base": 50, "bonus": 100},
        )

    def test_topo_order_is_stable_for_same_config(self):
        rules = [
            {"target": "x", "expr": "1", "type": "int32"},
            {"target": "y", "expr": "2", "type": "int32"},
            {"target": "z", "expr": "x + y", "type": "int32"},
        ]
        first = compile_mapping({"seed": "int32"}, rules).execution_order
        second = compile_mapping({"seed": "int32"}, rules).execution_order
        self.assertEqual(first, second)
        self.assertEqual(first, ["x", "y", "z"])

    def test_topo_order_follows_config_order_for_independent_rules(self):
        rules = [
            {"target": "b", "expr": "1", "type": "int32"},
            {"target": "a", "expr": "2", "type": "int32"},
        ]
        mapping = compile_mapping({"seed": "int32"}, rules)
        self.assertEqual(mapping.execution_order, ["b", "a"])


class TestConditions(unittest.TestCase):
    def _mapping(self):
        return compile_mapping(
            {"amount": "float"},
            [
                {
                    "target": "tier",
                    "expr": "'high'",
                    "type": "str",
                    "when": "amount >= 100",
                    "default": "low",
                }
            ],
        )

    def test_condition_true_branch(self):
        self.assertEqual(
            self._mapping().apply({"amount": 150.0}), {"tier": "high"}
        )

    def test_condition_false_branch_uses_default(self):
        self.assertEqual(
            self._mapping().apply({"amount": 10.0}), {"tier": "low"}
        )

    def test_condition_null_falls_back_to_default(self):
        self.assertEqual(
            self._mapping().apply({"amount": None}), {"tier": "low"}
        )

    def test_condition_false_without_default_yields_none(self):
        mapping = compile_mapping(
            {"amount": "float"},
            [
                {
                    "target": "tier",
                    "expr": "'high'",
                    "type": "str",
                    "when": "amount >= 100",
                }
            ],
        )
        self.assertEqual(mapping.apply({"amount": 1.0}), {"tier": None})


class TestNullPropagationAndDefaults(unittest.TestCase):
    def test_arithmetic_propagates_null(self):
        mapping = compile_mapping(
            {"a": "int32", "b": "int32"},
            [{"target": "total", "expr": "a + b", "type": "int32"}],
        )
        self.assertEqual(mapping.apply({"a": 1, "b": None}), {"total": None})

    def test_explicit_default_replaces_null(self):
        mapping = compile_mapping(
            {"a": "int32", "b": "int32"},
            [
                {
                    "target": "total",
                    "expr": "a + b",
                    "type": "int32",
                    "default": 0,
                }
            ],
        )
        self.assertEqual(mapping.apply({"a": 1, "b": None}), {"total": 0})

    def test_missing_source_key_reads_as_null(self):
        mapping = compile_mapping(
            {"nickname": "str"},
            [
                {
                    "target": "display",
                    "source": "nickname",
                    "type": "str",
                    "default": "anonymous",
                }
            ],
        )
        self.assertEqual(mapping.apply({}), {"display": "anonymous"})

    def test_function_call_propagates_null(self):
        mapping = compile_mapping(
            {"raw": "str"},
            [{"target": "shout", "expr": "upper(raw)", "type": "str"}],
        )
        self.assertEqual(mapping.apply({"raw": None}), {"shout": None})


class TestIntegerOverflow(unittest.TestCase):
    def test_int32_overflow_raises(self):
        mapping = compile_mapping(
            {"big": "int64"},
            [{"target": "small", "source": "big", "type": "int32"}],
        )
        with self.assertRaises(IntegerOverflowError):
            mapping.apply({"big": 2 ** 31})

    def test_int32_boundary_ok(self):
        mapping = compile_mapping(
            {"big": "int64"},
            [{"target": "small", "source": "big", "type": "int32"}],
        )
        self.assertEqual(
            mapping.apply({"big": 2 ** 31 - 1}), {"small": 2 ** 31 - 1}
        )

    def test_expression_result_overflow_raises(self):
        mapping = compile_mapping(
            {"a": "int32"},
            [{"target": "doubled", "expr": "a * 2", "type": "int8"}],
        )
        with self.assertRaises(IntegerOverflowError):
            mapping.apply({"a": 100})


class TestConfigErrors(unittest.TestCase):
    def test_cycle_detected(self):
        with self.assertRaises(CycleError) as ctx:
            compile_mapping(
                {"seed": "int32"},
                [
                    {"target": "a", "expr": "b + 1"},
                    {"target": "b", "expr": "a + 1"},
                ],
            )
        self.assertEqual(ctx.exception.cycle_fields, ["a", "b"])

    def test_self_cycle_detected(self):
        with self.assertRaises(CycleError):
            compile_mapping(
                {"seed": "int32"},
                [
                    {"target": "a", "expr": "b + 1"},
                    {"target": "b", "expr": "b + 1"},
                ],
            )

    def test_duplicate_target_rejected(self):
        with self.assertRaises(DuplicateTargetError):
            compile_mapping(
                {"x": "int32"},
                [
                    {"target": "y", "source": "x"},
                    {"target": "y", "expr": "x * 2"},
                ],
            )

    def test_unknown_source_field_rejected(self):
        with self.assertRaises(UnknownFieldError):
            compile_mapping(
                {"x": "int32"},
                [{"target": "y", "expr": "x + ghost"}],
            )

    def test_unknown_rename_source_rejected(self):
        with self.assertRaises(UnknownFieldError):
            compile_mapping(
                {"x": "int32"},
                [{"target": "y", "source": "ghost"}],
            )

    def test_unknown_field_in_condition_rejected(self):
        with self.assertRaises(UnknownFieldError):
            compile_mapping(
                {"x": "int32"},
                [{"target": "y", "expr": "x", "when": "ghost > 0"}],
            )

    def test_incompatible_type_rejected(self):
        with self.assertRaises(TypeMismatchError):
            compile_mapping(
                {"name": "str"},
                [{"target": "n", "source": "name", "type": "int32"}],
            )

    def test_float_to_int_without_cast_rejected(self):
        with self.assertRaises(TypeMismatchError):
            compile_mapping(
                {"ratio": "float"},
                [{"target": "n", "source": "ratio", "type": "int32"}],
            )

    def test_incompatible_derived_type_rejected(self):
        with self.assertRaises(TypeMismatchError):
            compile_mapping(
                {"a": "int32", "b": "int32"},
                [{"target": "bad", "expr": "a + b", "type": "str"}],
            )

    def test_source_and_expr_are_mutually_exclusive(self):
        with self.assertRaises(ConfigError):
            compile_mapping(
                {"x": "int32"},
                [{"target": "y", "source": "x", "expr": "x + 1"}],
            )

    def test_disallowed_syntax_rejected(self):
        with self.assertRaises(ConfigError):
            compile_mapping(
                {"x": "int32"},
                [{"target": "y", "expr": "__import__('os')"}],
            )

    def test_unknown_type_name_rejected(self):
        with self.assertRaises(ConfigError):
            compile_mapping(
                {"x": "int32"},
                [{"target": "y", "source": "x", "type": "decimal"}],
            )


class TestRuntimeErrors(unittest.TestCase):
    def test_division_by_zero(self):
        mapping = compile_mapping(
            {"a": "int32", "b": "int32"},
            [{"target": "r", "expr": "a / b", "type": "float"}],
        )
        with self.assertRaises(ExecutionError):
            mapping.apply({"a": 1, "b": 0})

    def test_runtime_type_mismatch(self):
        mapping = compile_mapping(
            {"a": "int32"},
            [{"target": "r", "source": "a", "type": "int32"}],
        )
        with self.assertRaises(ExecutionError):
            mapping.apply({"a": "not-an-int"})


if __name__ == "__main__":
    unittest.main()
