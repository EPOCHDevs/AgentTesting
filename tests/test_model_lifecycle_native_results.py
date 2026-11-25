import copy
from datetime import datetime
from pathlib import Path
import unittest

from tools.model_lifecycle_e2e.native_results import assert_tables
from tools.model_lifecycle_e2e.oracle import expected

FIXTURE = Path(__file__).resolve().parents[1] / "tools/model_lifecycle_e2e/fixtures/owned-observed-predictor.csv"


class NativeResultsTests(unittest.TestCase):
    def setUp(self):
        self.reference = expected(FIXTURE)["holdout"]
        self.tables = {
            "predictions": {"columns": ["coordinate", "prediction", "baseline", "actual"],
                            "rows": [[int(datetime.fromisoformat(row["coordinate"].replace("Z", "+00:00")).timestamp()) * 1_000_000_000,
                                      row["prediction"], row["baseline"], row["actual"]]
                                     for row in self.reference["rows"]]},
            "scores": {"columns": ["scored_count", "model_mse", "baseline_mse"],
                       "rows": [[self.reference[field] for field in ("scored_count", "model_mse", "baseline_mse")]]}}

    def test_complete_rows_and_scores(self):
        assert_tables(self.tables, self.reference)

    def test_rejects_partial_or_reordered_rows(self):
        for change in (lambda rows: rows.pop(), lambda rows: rows.reverse()):
            tables = copy.deepcopy(self.tables)
            change(tables["predictions"]["rows"])
            with self.assertRaises(AssertionError):
                assert_tables(tables, self.reference)

    def test_rejects_changed_baseline_even_with_correct_score(self):
        self.tables["predictions"]["rows"][0][2] += 0.01
        with self.assertRaises(AssertionError):
            assert_tables(self.tables, self.reference)

    def test_rejects_changed_scoring_sample(self):
        self.tables["scores"]["rows"][0][0] -= 1
        with self.assertRaises(AssertionError):
            assert_tables(self.tables, self.reference)
