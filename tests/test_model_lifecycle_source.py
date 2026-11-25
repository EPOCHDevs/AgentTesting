import csv
import tempfile
import unittest
from pathlib import Path

from tools.model_lifecycle_e2e.source import fixture_source

FIXTURE = Path(__file__).resolve().parents[1] / 'tools/model_lifecycle_e2e/fixtures/owned-observed-predictor.csv'


class ModelLifecycleSourceTest(unittest.TestCase):
    def test_fit_and_consumption_have_distinct_explicit_origins(self):
        fitted = fixture_source(FIXTURE, stage='fit')
        continued = fixture_source(FIXTURE, stage='continue', producer_execution='original-run')
        saved = fixture_source(FIXTURE, stage='saved', model_ref='model_' + 'a' * 64)
        self.assertEqual(fitted.count('= ols_fit('), 1)
        self.assertNotIn('ols_fit(', continued)
        self.assertNotIn('ols_fit(', saved)
        self.assertIn('fit_execution_id="original-run"', continued)
        self.assertNotIn('fit_execution_id=', saved)
        for source in (fitted, continued, saved):
            self.assertIn('response_table(name="predictions"', source)
            self.assertIn('response_table(name="scores"', source)
            self.assertIn('Explicitly synthetic owned integration fixture', source)
            self.assertNotIn('save_as=', source)
        self.assertIn('start="2022-01-03", end="2022-01-26"', fitted)
        self.assertIn('start="2024-01-02", end="2024-01-09"', fitted)
        self.assertIn('start="2025-01-02", end="2025-01-07"', continued)

    def test_continuation_cannot_omit_or_change_origin_mode(self):
        for kwargs in ({'stage': 'continue'}, {'stage': 'saved', 'producer_execution': 'run'},
                       {'stage': 'fit', 'producer_execution': 'run'},
                       {'stage': 'continue', 'producer_execution': 'bad\nrun'},
                       {'stage': 'fit', 'model_ref': 'bad")\nnew_fit('}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                fixture_source(FIXTURE, **kwargs)

    def test_invalid_dates_and_overlapping_partitions_are_not_silently_repaired(self):
        with FIXTURE.open(newline='') as stream:
            rows = list(csv.DictReader(stream))
        for changes in ({'coordinate': '2022-01-03T00:00:01Z'},
                        {'partition': 'wrong'}, {'coordinate': rows[1]['coordinate']},
                        {'coordinate': '2025-01-10T00:00:00Z'}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as folder:
                modified = [dict(row) for row in rows]
                modified[0].update(changes)
                path = Path(folder) / 'fixture.csv'
                with path.open('w', newline='') as stream:
                    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                    writer.writeheader()
                    writer.writerows(modified)
                with self.assertRaises(ValueError):
                    fixture_source(path, stage='fit')


if __name__ == '__main__':
    unittest.main()
