import unittest
from fractions import Fraction as F
from tools.model_lifecycle_e2e.oracle import Row, assert_close, fit, number, values


class ModelLifecycleOracleTest(unittest.TestCase):
    def test_independent_closed_form_and_untouched_future(self):
        # y=2+3x on the training relation; future targets must never enter fitting.
        rows = [Row(str(i), F(i), F(2+3*i), "train") for i in range(4)]
        rows += [Row("next", F(5), F(-1000), "continuation")]
        model = fit(rows)
        self.assertEqual((model.intercept, model.slope, model.baseline), (F(2), F(3), F(13, 2)))
        result = values(model, rows, "continuation")
        self.assertEqual(result["rows"][0]["prediction"], 17)
        self.assertEqual(result["model_mse"], 1017**2)

    def test_baseline_uses_the_same_joint_finite_pairs(self):
        rows = [Row("a", F(0), F(1), "train"), Row("b", F(1), F(3), "train"),
                Row("excluded", None, F(1000), "train")]
        model = fit(rows)
        self.assertEqual(model.baseline, 2)
        self.assertEqual(model.training_count, 2)
        self.assertIsNone(model.predict(None))

    def test_constant_predictor_and_insufficient_rows_reject(self):
        with self.assertRaisesRegex(ValueError, "rank deficient"):
            fit([Row("a", F(1), F(2), "train"), Row("b", F(1), F(3), "train")])
        with self.assertRaisesRegex(ValueError, "at least two"):
            fit([Row("a", F(1), F(2), "train")])

    def test_duplicate_coordinates_preserve_distinct_rows(self):
        rows = [Row("same", F(0), F(2), "train"), Row("same", F(1), F(5), "train")]
        self.assertEqual(fit(rows).slope, 3)
        self.assertEqual(fit(rows).training_count, 2)
        # Coordinate membership alone does not encode values; source_sha256 pins
        # the complete input bytes separately. Duplicate rows are never deduped.
        self.assertEqual(fit(rows).selected_rows_sha256, fit(list(reversed(rows))).selected_rows_sha256)
        changed_coordinate = [rows[0], Row("different", F(1), F(5), "train")]
        self.assertNotEqual(fit(rows).selected_rows_sha256, fit(changed_coordinate).selected_rows_sha256)

    def test_nonfinite_values_are_missing_and_missing_evidence_is_not_zero(self):
        for raw in ["", "NaN", "Infinity", "-Infinity", None]:
            self.assertIsNone(number(raw))
        self.assertEqual(number("0.0001"), F(1, 10000))
        with self.assertRaises(ValueError):
            number("not a number")
        for value in [None, float("nan"), float("inf"), 2.0]:
            with self.assertRaises(AssertionError):
                assert_close(value, 1.0)
        assert_close(1+1e-13, 1.0)
        with self.assertRaises(AssertionError):
            assert_close(0, None)


if __name__ == "__main__":
    unittest.main()
