"""Source/reference controls only; no Native execution or family verdict."""
import ast
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.model_lifecycle_e2e import family_consumers as driver
from tools.model_lifecycle_e2e.families import synthetic_value
from tools.model_lifecycle_e2e.family_forecasts import retained_contract


def preparation_case():
    return driver.Case('HMM', 'temporary', 'http://127.0.0.1:9000', 'http://127.0.0.1:9010',
        Path('/PREPARATION_ONLY/native'), 'b'*40, 'fixture', 'producer', 'owned',
        'sha256:'+'a'*64, 'c'*64, 'd'*64, '2022-06-09T00:00:00Z',
        Path('/PREPARATION_ONLY/artifact'), 'owned', None,
        Path('/PREPARATION_ONLY/provider'), 'e'*64, 'f'*64, '0'*64, '1'*64)


def observed():
    first = 1_654_819_200_000_000_000
    return {'columns': ['coordinate', 'observed', 'p0', 'p1'], 'rows': [
        [first+i*86_400_000_000_000, synthetic_value(160+i), .2, .8] for i in range(40)]}


class FamilyReplayControlsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.case = preparation_case()

    def ref(self, name, value):
        path = self.root/name
        path.write_text(json.dumps(value))
        return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

    def reference(self):
        seal = {'role': 'Consumer', 'owner_user_id': self.case.owner, 'family': 'HMM',
            'model_ref': self.case.fit_name, 'binding_kind': 'ExecutionFit',
            'source_execution_id': self.case.producer, 'capability': 'ObservedInference',
            'resolved_binding': {'digest': self.case.digest, 'fit_cutoff': self.case.fit_cutoff}}
        node = {'id': 'base', 'type': 'hmm_infer', 'model_output_use': 'ObservedCausal',
            'model_artifact_contract_json': json.dumps(seal), 'runtime_contract_json': json.dumps({
                'modelOutputUse': 'ObservedCausal', 'modelCapabilities': ['ObservedInference']})}
        config = self.ref('config.json', {'execution_plan': {'nodes': [node]}})
        witness = retained_contract(Path(config['path']), family='HMM', digest=self.case.digest,
            owner=self.case.owner, producer=self.case.producer, fit_cutoff=self.case.fit_cutoff,
            manifest_sha256=None, model_ref=self.case.fit_name)
        receipt = {'status': 'passed', 'family': 'HMM', 'stage': 'temporary',
            'digest': self.case.digest, 'producer': self.case.producer, 'model_ref': self.case.fit_name,
            'manifest_sha256': None, 'executions': ['base-run', 'suffix-run'],
            'fit_operations_per_consumer': [0, 0], 'contract_witnesses': [asdict(witness), asdict(witness)],
            'numeric': {'causal_prefix_rows': 5, 'observed_rows_per_consumer': 40}}
        return {'hmm_reference_receipt': self.ref('receipt.json', receipt),
            'hmm_reference_answers': self.ref('answers.json', observed()), 'hmm_reference_config': config}

    def load(self, value):
        return driver.load_hmm_reference(value, owner=self.case.owner, producer=self.case.producer,
            fit_name=self.case.fit_name, digest=self.case.digest, fit_cutoff=self.case.fit_cutoff)

    def test_hmm_lifecycle_keeps_two_plus_one_plus_one_consumer_bound(self):
        saved = replace(self.case, stage='saved', model_ref='model_'+'a'*64, manifest_sha256='b'*64)
        cleaned = replace(saved, stage='post_cleanup')
        sources = [driver.sources(case) for case in (self.case, saved, cleaned)]
        self.assertEqual([len(group) for group in sources], [2, 1, 1])
        self.assertEqual(sources[1], sources[2])
        for group in sources:
            self.assertTrue(all('hmm_fit(' not in source for source in group))

    def test_each_arima_garch_stage_retains_four_actual_empty_input_forecasts(self):
        for family in ('ARIMA', 'GARCH'):
            for stage in ('temporary', 'saved', 'post_cleanup'):
                case = replace(self.case, family=family, stage=stage,
                    model_ref='owned' if stage=='temporary' else 'model_'+'a'*64,
                    manifest_sha256=None if stage=='temporary' else 'b'*64)
                sources = driver.sources(case)
                self.assertEqual(len(sources), 1)
                calls = [node for node in ast.walk(ast.parse(sources[0]))
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
                forecasts = [node for node in calls if node.func.id==family.lower()+'_forecast_from_fit']
                self.assertEqual(len(forecasts), 4)
                self.assertTrue(all(not node.args for node in forecasts))
                self.assertEqual(sorted(next(k.value.value for k in node.keywords if k.arg=='horizon')
                    for node in forecasts), [1, 2, 5, 21])
                self.assertFalse(any(node.func.id in {'arima_fit', 'garch_fit', 'arima_infer', 'garch_infer',
                    'reference_assets', 'series', 'clip_window'} for node in calls))

    def test_saved_hmm_checks_all_rows_and_refuses_a_normalized_late_change(self):
        driver.compare_hmm_replay(observed(), observed())
        changed = observed()
        changed['rows'][30][2:4] = [.3, .7]
        with self.assertRaisesRegex(AssertionError, 'changed a row'):
            driver.compare_hmm_replay(observed(), changed)
        changed = observed()
        changed['rows'][3][2:4] = [True, False]
        with self.assertRaisesRegex(AssertionError, 'Invalid HMM'):
            driver.compare_hmm_replay(observed(), changed)

    def test_reference_requires_temporary_pair_owner_digest_and_config_witness(self):
        value = self.reference()
        reference = self.load(value)
        self.assertEqual(driver.read_hmm_reference(reference), observed())
        for key in tuple(value):
            missing = {k: v for k, v in value.items() if k!=key}
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'original temporary base'):
                self.load(missing)
        receipt = json.loads(Path(value['hmm_reference_receipt']['path']).read_text())
        for update in ({'digest': 'sha256:'+'0'*64}, {'producer': 'other'}, {'stage': 'saved'},
                       {'fit_operations_per_consumer': [0, 1]}):
            changed = {**value, 'hmm_reference_receipt': self.ref('bad-receipt.json', receipt|update)}
            with self.subTest(update=update), self.assertRaisesRegex(ValueError, 'temporary causal pair'):
                self.load(changed)
        with self.assertRaisesRegex(AssertionError, 'selected binding'):
            driver.load_hmm_reference(value, owner='another', producer=self.case.producer,
                fit_name=self.case.fit_name, digest=self.case.digest, fit_cutoff=self.case.fit_cutoff)

    def test_mutated_hmm_reference_stops_before_credential_or_sdk_access(self):
        value = self.reference()
        reference = self.load(value)
        Path(reference.answers).write_text('{}')
        case = replace(self.case, stage='saved', hmm_reference=reference)
        destination = self.root/'new-attempt'
        with self.assertRaisesRegex(ValueError, 'answer bytes changed'):
            driver.run(case, destination, lambda: self.fail('No credential access on changed reference'))
        self.assertFalse(destination.exists())
