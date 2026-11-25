"""Source/admission controls only; no credentials, SDKs or native calls."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.model_lifecycle_e2e import family_consumers as driver
from tools.model_lifecycle_e2e.families import family_source, synthetic_value
from tools.model_lifecycle_e2e.family_forecasts import forecast_source, retained_contract
from tests.test_model_lifecycle_forecast_oracle import garch


class FamilyConsumerSourceTest(unittest.TestCase):
    def test_forecasts_have_four_exact_empty_input_calls_and_one_origin(self):
        for family in ('ARIMA', 'GARCH'):
            source = forecast_source(family, model_ref='owned', producer_execution='producer')
            self.assertEqual(source.count('_forecast_from_fit('), 4)
            self.assertEqual(source.count('fit_execution_id="producer")()'), 4)
            for h in (1, 2, 5, 21):
                self.assertIn(f'horizon={h},', source)
                self.assertIn(f'CAST({h} AS BIGINT)', source)
            self.assertNotIn('study_assets', source)
            self.assertNotIn('arima_fit(', source)
            self.assertNotIn('garch_fit(', source)
            self.assertIn('response_table(name="forecasts"', source)
        for kwargs in ({}, {'producer_execution': 'x', 'manifest_sha256': 'a'*64}):
            with self.assertRaises(ValueError):
                forecast_source('ARIMA', model_ref='owned', **kwargs)

    def test_hmm_intervention_preserves_training_and_first_five_observed_rows(self):
        base = family_source('HMM', model_ref='owned', producer_execution='p')
        changed = family_source('HMM', model_ref='owned', producer_execution='p', changed_observed_suffix=True)
        self.assertEqual(base.split('(165,')[0], changed.split('(165,')[0])
        self.assertNotEqual(base, changed)
        with self.assertRaises(ValueError):
            family_source('HMM', model_ref='owned', fit=True, changed_observed_suffix=True)

    def test_prefix_assertion_rejects_suffix_leak_and_false_intervention(self):
        columns = ['coordinate', 'observed', 'p0', 'p1']
        base = {'columns': columns, 'rows': [[i, synthetic_value(160+i), .2, .8] for i in range(40)]}
        changed = {'columns': columns, 'rows': [[i, synthetic_value(160+i)*(-1 if i>=5 else 1), .3 if i>=5 else .2, .7 if i>=5 else .8] for i in range(40)]}
        driver.compare_hmm_prefix(base, changed)
        changed['rows'][0][2:4] = [.4, .6]
        with self.assertRaisesRegex(AssertionError, 'revised an earlier'):
            driver.compare_hmm_prefix(base, changed)
        with self.assertRaisesRegex(AssertionError, 'did not actually change'):
            driver.compare_hmm_prefix(base, base)


class ConsumerAdmissionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.provider = self.root/'provider.py'
        self.provider.write_text('def get_id_token():\n    raise AssertionError("No provider calls")\n')
        sha = hashlib.sha256(self.provider.read_bytes()).hexdigest()
        self.addCleanup(patch.stopall)
        patch.object(driver, 'TOKEN_PROVIDER_PATH', self.provider).start()
        patch.object(driver, 'TOKEN_PROVIDER_SHA256', sha).start()
        raw = garch();self.artifact = self.root/'artifact';self.artifact.write_bytes(raw)
        digest = 'sha256:'+hashlib.sha256(raw).hexdigest()
        # Actual artifact manifest shape; no model is estimated or invoked.
        from tools.model_lifecycle_e2e.native_artifact import ENVELOPE
        import struct
        start=len(ENVELOPE)+8;length=struct.unpack_from('>Q',raw,len(ENVELOPE))[0]
        manifest=json.loads(raw[start:start+length])
        self.runtime={'kind':'ROOT_ADMITTED_PRIMARY_MODEL_LIFECYCLE_RUNTIME_V1','ready':True,
            'native_url':'http://127.0.0.1:9000','platform_url':'http://127.0.0.1:9010',
            'native_root':str(self.root),'native_source':'b'*40,'owner_user_id':'fixture'}
        source='owned original source'
        producer={'owner_user_id':'fixture','execution_id':'fit-run','storage_location':'server',
            'producer_config_sha256':'c'*64,'producer':{'study':{'source':source}},
            'fits':[{'fit_name':'owned','artifact':{'binding':{'digest':digest},'manifest':manifest}}]}
        self.value={'kind':'ROOT_ADMITTED_MODEL_LIFECYCLE_CONSUMER_V1','ready':True,
            **{k:v for k,v in self.runtime.items() if k not in {'kind','ready'}},
            'family':'GARCH','stage':'temporary','producer_execution_id':'fit-run','fit_name':'owned',
            'digest':digest,'producer_config_sha256':'c'*64,
            'producer_source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'fit_cutoff':'2022-06-09T00:00:00Z','storage_location':'server',
            'artifact_path':str(self.artifact),'token_provider_file':str(self.provider),
            'token_provider_sha256':sha,'root_runtime_packet':self.ref('runtime.json',self.runtime),
            'producer_descriptor':self.ref('producer.json',producer)}

    def ref(self, name, value):
        p=self.root/name;p.write_text(json.dumps(value));return {'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}

    def load(self):
        p=self.root/'case.json';p.write_text(json.dumps(self.value))
        return driver.load_case(p,hashlib.sha256(p.read_bytes()).hexdigest())

    def test_root_source_owner_origins_artifact_and_provider_join_without_calls(self):
        case=self.load()
        self.assertEqual(case.owner,'fixture')
        self.assertEqual(len(driver.sources(case)),1)
        self.assertIn('fit_execution_id="fit-run"',driver.sources(case)[0])
        expected=driver.garch_future(driver.decode_garch(self.artifact.read_bytes(),digest=case.digest,owner=case.owner,execution=case.producer),1)
        self.assertGreater(expected.variance,0)

    def test_changed_readiness_and_unknown_cleanup_are_refused(self):
        self.runtime['ready']=False
        self.value['root_runtime_packet']=self.ref('runtime.json',self.runtime)
        with self.assertRaisesRegex(ValueError,'primary readiness'):
            self.load()
        self.runtime['ready']=True
        self.value['root_runtime_packet']=self.ref('runtime.json',self.runtime)
        self.value.update(stage='post_cleanup',manifest_sha256='a'*64,saved_model_ref='model_foo')
        self.value['supported_cleanup_witness']=self.ref('cleanup.json',{'root_admitted':False})
        with self.assertRaisesRegex(ValueError,'supported cleanup'):
            self.load()

    def test_generation_owner_and_payload_never_fall_back(self):
        for key,value in [('owner_user_id','another'),('digest','sha256:'+'0'*64),('storage_location','shared_personal_v1')]:
            original=self.value[key];self.value[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):self.load()
            self.value[key]=original

    def test_missing_opaque_token_or_existing_attempt_stops_before_sdk_work(self):
        case=self.load();destination=self.root/'attempt'
        with self.assertRaisesRegex(ValueError,'no opaque credential'):
            driver.run(case,destination,lambda:None)
        self.assertFalse(destination.exists())
        destination.mkdir()
        with self.assertRaises(FileExistsError):
            driver.run(case,destination,lambda: self.fail('No credential refresh on resubmit'))


class RetainedContractTest(unittest.TestCase):
    def test_forecast_origin_owner_horizon_and_actual_causal_stamps(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'config.json'
            nodes=[]
            for h in (1,2,5,21):
                seal={'role':'Consumer','owner_user_id':'fixture','family':'ARIMA','model_ref':'owned',
                    'resolved_binding':{'digest':'sha256:'+'a'*64,'fit_cutoff':'2022-06-09T00:00:00Z'},
                    'source_execution_id':'producer','binding_kind':'ExecutionFit','capability':'ForecastCapable',
                    'as_of':'2022-06-09T00:00:00Z','forecast':{'origin_policy':'FitCheckpoint',
                    'origin_input_id':'__model_artifact','horizon_unit':'InputObservations',
                    'target_policy':'OriginPlusHorizon','horizon_count':h}}
                nodes.append({'id':f'f{h}','type':'arima_forecast_from_fit','model_output_use':'ObservedCausal',
                    'runtime_contract_json':json.dumps({'modelOutputUse':'ObservedCausal','modelCapabilities':['ForecastCapable']}),
                    'model_artifact_contract_json':json.dumps(seal)})
            def check():
                path.write_text(json.dumps({'execution_plan':{'nodes':nodes}}))
                return retained_contract(path,family='ARIMA',digest='sha256:'+'a'*64,owner='fixture',
                    producer='producer',fit_cutoff='2022-06-09T00:00:00Z',manifest_sha256=None,model_ref='owned')
            self.assertEqual(check().horizons,(1,2,5,21))
            nodes[0]['model_output_use']='RetrospectiveOnly'
            with self.assertRaisesRegex(AssertionError,'causal mode'):
                check()
            nodes[0]['model_output_use']='ObservedCausal'
            original=json.loads(nodes[0]['model_artifact_contract_json']);original['as_of']='2025-01-31T00:00:00Z'
            nodes[0]['model_artifact_contract_json']=json.dumps(original)
            with self.assertRaisesRegex(AssertionError,'checkpoint origin'):
                check()


if __name__=='__main__':unittest.main()
