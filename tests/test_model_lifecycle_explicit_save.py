"""Offline orchestration controls; no SDK/server acceptance is claimed."""
from dataclasses import asdict, dataclass
from pathlib import Path
import tempfile
from threading import Lock
import unittest

from tools.model_lifecycle_e2e.explicit_save import save_with_changed_ids_and_names
from tools.model_lifecycle_e2e.families import family_source
from tools.model_lifecycle_e2e.source import fixture_source

FIXTURE = Path(__file__).resolve().parents[1] / 'tools/model_lifecycle_e2e/fixtures/owned-observed-predictor.csv'


@dataclass
class Saved:
    model_ref: str
    name: str

    def model_dump(self, **_):
        return asdict(self)


class ExplicitSaveControlsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_three_distinct_ids_and_names_return_the_original_model(self):
        lock = Lock()
        calls = []
        stored = []

        def save(ref, name):
            with lock:
                calls.append((ref, name))
                if not stored:
                    stored.append(Saved('original', name))
                return stored[0]

        result = save_with_changed_ids_and_names(save, self.root)
        self.assertEqual(result, stored[0])
        self.assertEqual(len({ref for ref, _ in calls}), 3)
        self.assertEqual(len({name for _, name in calls}), 3)
        self.assertEqual(len(list(self.root.glob('first-save-*-response.json'))), 2)

    def test_duplicate_models_fail_before_a_ready_retry(self):
        calls = []

        def save(ref, name):
            calls.append(ref)
            return Saved(ref, name)

        with self.assertRaisesRegex(AssertionError, 'different models'):
            save_with_changed_ids_and_names(save, self.root)
        self.assertEqual(len(calls), 2)
        self.assertFalse((self.root/'ready-repeat-intent.json').exists())

    def test_unconfirmed_first_batch_is_preserved_without_a_replacement_request(self):
        calls = []

        def save(ref, name):
            calls.append(ref)
            raise TimeoutError('Unknown outcome')

        with self.assertRaises(TimeoutError):
            save_with_changed_ids_and_names(save, self.root)
        self.assertGreaterEqual(len(calls), 1)
        self.assertLessEqual(len(calls), 2)
        self.assertTrue((self.root/'first-save-intents.json').exists())
        self.assertEqual(len(list(self.root.glob('first-save-*-unconfirmed.json'))), len(calls))
        self.assertFalse((self.root/'ready-repeat-intent.json').exists())

    def test_source_builders_do_not_accept_a_save_as_instruction(self):
        with self.assertRaises(TypeError):
            fixture_source(FIXTURE, stage='fit', save_as='retired')
        for family in ('HMM', 'ARIMA', 'GARCH'):
            with self.subTest(family=family), self.assertRaises(TypeError):
                family_source(family, model_ref='owned', fit=True, save_as='retired')


if __name__ == '__main__':
    unittest.main()
