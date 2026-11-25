"""Opaque injected credentials only; no provider, SDK or service invocation."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from tools.model_lifecycle_e2e.opaque_auth import admit_primary, caller
from tools.model_lifecycle_e2e.notebook import notebook
from tools.model_lifecycle_e2e.owned_primary import run


class OpaqueOwnedAuthTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.packet={'kind':'ROOT_ADMITTED_PRIMARY_MODEL_LIFECYCLE_RUNTIME_V1','ready':True,
            'native_url':'http://127.0.0.1:9000','platform_url':'http://127.0.0.1:9010',
            'native_root':str(self.root),'native_source':'b'*40,'owner_user_id':'fixture'}
        self.path=self.root/'ready.json';self.path.write_text(json.dumps(self.packet))
        self.sha=hashlib.sha256(self.path.read_bytes()).hexdigest()

    def test_opaque_non_jwt_is_never_interpreted_as_identity(self):
        token,owner=caller('actual-expected-owner',lambda:'opaque-credential-with-no-claims')
        self.assertEqual(token,'opaque-credential-with-no-claims')
        self.assertEqual(owner,'actual-expected-owner')

    def test_missing_expected_owner_or_opaque_provider_result_stops(self):
        for token in (None,''):
            with self.assertRaisesRegex(ValueError,'no opaque credential'):
                caller('fixture',lambda:token)
        with self.assertRaisesRegex(ValueError,'expected owned-resource'):
            caller('',lambda:self.fail('No credential refresh without admitted owner'))

    def test_falsey_injected_provider_does_not_select_an_identity_fallback(self):
        class FalseyProvider:
            def __bool__(self):return False
            def __call__(self):return None
        with self.assertRaisesRegex(ValueError,'no opaque credential'):
            caller('fixture',FalseyProvider())

    def test_actual_root_source_owner_and_origins_must_join(self):
        def check(owner='fixture',url='http://127.0.0.1:9000'):
            return admit_primary(self.path,self.sha,native_url=url,
                platform_url=self.packet['platform_url'],native_root=self.root,expected_owner=owner)
        self.assertEqual(check().native_source,'b'*40)
        for args in ({'owner':'other'},{'url':'http://127.0.0.1:9999'}):
            with self.assertRaisesRegex(ValueError,'admission differs'):check(**args)
        self.packet['ready']=False;self.path.write_text(json.dumps(self.packet))
        with self.assertRaisesRegex(ValueError,'readiness bytes'):check()

    def test_owned_primary_none_stops_before_sdk_or_attempt(self):
        args=SimpleNamespace(native_root=self.root,fixture=self.root/'fixture.csv',
            evidence=self.root/'attempt',root_readiness=self.path,root_readiness_sha256=self.sha,
            native_url=self.packet['native_url'],platform_url=self.packet['platform_url'],expected_owner='fixture')
        with self.assertRaisesRegex(ValueError,'no opaque credential'):
            run(args,lambda:None)
        self.assertFalse(args.evidence.exists())

    def test_notebook_source_has_no_cache_claims_or_token_file_path(self):
        output=self.root/'source.ipynb'
        notebook(Path(__file__).resolve().parents[1],self.root/'passed.json',self.root/'attempt',output)
        value=json.loads(output.read_text())
        code='\n'.join(''.join(c['source']) for c in value['cells'] if c['cell_type']=='code')
        self.assertIn('caller(original["owner"])',code)
        self.assertIn('admit_primary(',code)
        for forbidden in ('local-login.json','Path.home()','id_token','urlsafe_b64decode','jwt','profile','groups'):
            self.assertNotIn(forbidden,code)
        for index,cell in enumerate(value['cells']):
            if cell['cell_type']=='code':compile(''.join(cell['source']),f'notebook-cell-{index}','exec')


if __name__=='__main__':unittest.main()
