"""Offline admission/routing/capture controls. No provider, SDK or live requests."""
import copy
import gzip
import hashlib
import json
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

from tests import test_model_lifecycle_family_consumers as fixtures
from tools.model_lifecycle_e2e import family_cloud as cloud, family_consumers as driver
from tools.model_lifecycle_e2e.family_forecasts import forecast_source
from tools.model_lifecycle_e2e.native_artifact import ENVELOPE

OWNER = '9939691e-0041-706e-845f-0bd7eb3dd06d'
PRODUCER = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
EXECUTION = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
DEFINITION = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
CASE = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd'


def http_reply(status, *, value=None, content=None, headers=None):
    import httpx
    raw = json.dumps(value, separators=(',', ':')).encode() if content is None else content
    return httpx.Response(status, stream=httpx.ByteStream(raw), headers=headers)


class CloudTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ConsumerAdmissionTest('test_root_source_owner_origins_artifact_and_provider_join_without_calls')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.value = self.fixture.root, self.fixture.value
        self.runtime = self.fixture.runtime
        raw = self.fixture.artifact.read_bytes()
        size = struct.unpack_from('>Q', raw, len(ENVELOPE))[0]
        start = len(ENVELOPE) + 8
        manifest = json.loads(raw[start:start + size])
        manifest.update(owner_user_id=OWNER, producer_execution_id=PRODUCER)
        encoded = json.dumps(manifest).encode()
        raw = ENVELOPE + struct.pack('>Q', len(encoded)) + encoded + raw[start + size:]
        self.fixture.artifact.write_bytes(raw)
        descriptor = json.loads(Path(self.value['producer_descriptor']['path']).read_text())
        descriptor.update(owner_user_id=OWNER, execution_id=PRODUCER, definition_id=DEFINITION, storage_location='shared_personal_v1')
        descriptor['fits'][0]['artifact'].update(manifest=manifest, binding={'digest': 'sha256:' + hashlib.sha256(raw).hexdigest()})
        self.value['producer_descriptor'] = self.ref('producer.json', descriptor)
        plan_ref = self.ref('v6.json', {'fixture': 'Synthetic bytes, never actual v6 admission'})
        self.addCleanup(patch.stopall)
        patch.object(cloud, 'V6_SHA256', plan_ref['sha256']).start()
        self.runtime.update(kind='ROOT_ADMITTED_DEV_MODEL_LIFECYCLE_RUNTIME_V1', native_url=cloud.NATIVE,
            platform_url=cloud.PLATFORM, native_root=None, owner_user_id=OWNER, platform_source='e' * 40,
            runtime_receipts={'native': self.ref('native.json', {'offline': True}),
                              'platform': self.ref('platform.json', {'offline': True})})
        self.value.update(mode='dev-cloud', native_url=cloud.NATIVE, platform_url=cloud.PLATFORM,
            native_root=None, owner_user_id=OWNER, producer_execution_id=PRODUCER, platform_source='e' * 40,
            storage_location='shared_personal_v1', digest=descriptor['fits'][0]['artifact']['binding']['digest'],
            case_id=CASE, producer_definition_id=DEFINITION, consumer_execution_ids=[EXECUTION], acceptance_plan=plan_ref,
            harness_sources=cloud.source_hashes(), completed_generation='generation-1',
            completed_generation_witness=self.ref('generation.json', {'root_admitted': True,
                'status': 'Completed', 'execution_id': PRODUCER, 'definition_id': DEFINITION, 'owner_user_id': OWNER,
                'generation': 'generation-1', 'producer_config_sha256': 'c' * 64}),
            request_plan=cloud.stage_plan('GARCH', 'temporary'))
        self.refresh_source()

    def ref(self, name, value):
        return self.fixture.ref(name, value)

    def refresh_source(self):
        source = forecast_source('GARCH', model_ref=self.value.get('saved_model_ref') or 'owned',
            **({'manifest_sha256': self.value['manifest_sha256']} if self.value.get('manifest_sha256')
               else {'producer_execution': PRODUCER}))
        self.value['consumer_source_sha256'] = [hashlib.sha256(source.encode()).hexdigest()]

    def load(self, mode='dev-cloud'):
        self.value['root_runtime_packet'] = self.ref('runtime.json', self.runtime)
        p = self.root / 'case.json'
        p.write_text(json.dumps(self.value))
        return driver.load_case(p, hashlib.sha256(p.read_bytes()).hexdigest(), mode=mode)

    def test_cloud_is_double_opt_in_and_sources_are_bound_without_credentials(self):
        case = self.load()
        self.assertIsNone(case.native_root)
        self.assertEqual(case.cloud.executions, (EXECUTION,))
        self.assertEqual(case.native_url, cloud.NATIVE)
        with self.assertRaisesRegex(ValueError, 'explicit matching'):
            self.load(mode='loopback')
        self.value['consumer_source_sha256'] = ['0' * 64]
        with self.assertRaisesRegex(ValueError, 'source differs'):
            self.load()

    def test_changed_origins_and_mixed_local_cloud_roots_are_refused(self):
        for field, url in [('native_url', 'http://api.dev.epoch.trade'),
                           ('native_url', cloud.NATIVE + '/'), ('native_url', cloud.NATIVE + ':443'),
                           ('native_url', 'https://api.dev.epoch.trade.evil.invalid'),
                           ('native_url', 'https://user@api.dev.epoch.trade'),
                           ('platform_url', 'https://services.dev.epoch.trade'),
                           ('platform_url', 'https://services.epoch.trade')]:
            value, runtime = copy.deepcopy(self.value), copy.deepcopy(self.runtime)
            self.value[field] = self.runtime[field] = url
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, 'canonical'):
                self.load()
            self.value, self.runtime = value, runtime
        self.value['native_root'] = self.runtime['native_root'] = str(self.root)
        with self.assertRaisesRegex(ValueError, 'masquerade'):
            self.load()

    def test_saved_models_use_public_api_host_and_refuse_internal_service_host(self):
        import httpx
        self.value.update(stage='saved', saved_model_ref='model_' + 'a' * 64,
                          manifest_sha256='f' * 64, request_plan=cloud.stage_plan('GARCH', 'saved'))
        self.refresh_source()
        case = self.load()
        self.assertEqual(case.platform_url, 'https://api.dev.epoch.trade')
        path = '/api/v1/models/' + case.model_ref
        facts = {'model': {'model_ref': case.model_ref, 'digest': case.digest,
            'manifest_sha256': case.manifest_sha256, 'producer_execution_id': case.producer,
            'producer_fit_name': case.fit_name, 'producer_definition_id': DEFINITION,
            'producer_config_sha256': case.config_sha256}, 'manifest': {'owner_user_id': case.owner}}
        forwarded = []
        def handler(request):
            forwarded.append((request.method, str(request.url)))
            self.assertEqual(forwarded[-1], ('GET', 'https://api.dev.epoch.trade' + path))
            return http_reply(200, value=facts)
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            wire = cloud.Wire(client, cloud.RouteGuard(case), self.root)
            with self.assertRaisesRegex(ValueError, 'origin'):
                wire.call('GET', path, origin='https://services.dev.epoch.trade')
            self.assertEqual(forwarded, [])
            self.assertEqual(cloud.selection(wire, case), facts)
        self.assertEqual(forwarded, [('GET', 'https://api.dev.epoch.trade' + path)])

    def test_runtime_generation_source_and_slot_tampering_fail_closed(self):
        for field, value in [('platform_source', 'a' * 40), ('consumer_execution_ids', [PRODUCER]),
                             ('consumer_execution_ids', [EXECUTION, EXECUTION]),
                             ('request_plan', {**cloud.stage_plan('GARCH', 'temporary'), 'consumer_executions': 2}),
                             ('harness_sources', {}), ('completed_generation', 'other')]:
            original = self.value[field]
            self.value[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.load()
            self.value[field] = original
        p = Path(self.runtime['runtime_receipts']['native']['path'])
        p.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            self.load()

    def test_cloud_post_cleanup_cannot_use_generation_only_witness(self):
        self.value.update(stage='post_cleanup', saved_model_ref='model_' + 'a' * 64,
                          manifest_sha256='f' * 64, request_plan=cloud.stage_plan('GARCH', 'post_cleanup'))
        self.refresh_source()
        self.value['supported_cleanup_witness'] = self.ref('cleanup.json', {'root_admitted': True,
            'owner_user_id': OWNER, 'producer_execution_id': PRODUCER, 'digest': self.value['digest'],
            'source_execution_removed': True, 'temporary_artifact_removed': False})
        with self.assertRaisesRegex(ValueError, 'Generation deletion alone'):
            self.load()

    def test_routing_binds_exact_write_order_body_and_read_caps(self):
        guard = cloud.RouteGuard(self.load())
        for method, url in [('POST', cloud.NATIVE + '/api/v1/models'),
                            ('GET', cloud.NATIVE + '/api/v1/studies/executions/' + PRODUCER + '/export'),
                            ('DELETE', cloud.NATIVE + '/api/v1/studies/executions/' + PRODUCER),
                            ('GET', cloud.NATIVE + '/api/v1/studies/executions/' + EXECUTION + '/outputs'),
                            ('GET', 'https://other.invalid/health'),
                            ('GET', cloud.NATIVE + '/health?other=1')]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                guard.check(method, url)
        guard.check('GET', cloud.NATIVE + '/health')
        with self.assertRaises(ValueError): guard.check('GET', cloud.NATIVE + '/health')
        source = driver.sources(self.load())[0]
        body = {'source': source, 'study_assets': [], 'study_type': 'research'}
        with self.assertRaises(ValueError): guard.expect_post('/api/v1/models', body)
        guard.expect_post('/api/v1/script-validator/compile-view', body)
        with self.assertRaises(ValueError):
            guard.check('POST', cloud.NATIVE + '/api/v1/script-validator/compile-view', {**body, 'source': 'different'})
        guard.check('POST', cloud.NATIVE + '/api/v1/script-validator/compile-view', body)
        with self.assertRaises(ValueError): guard.check('POST', cloud.NATIVE + '/api/v1/script-validator/compile-view', body)
        with self.assertRaises(ValueError): guard.expect_post('/api/v1/studies/executions', {})

    def test_unbound_wrong_page_and_early_arrow_reads_are_refused(self):
        guard = cloud.RouteGuard(self.load())
        ref = {'table': 'actual-table', 'field_ids': ['a'], 'row_count': 4}
        with self.assertRaisesRegex(ValueError, 'completed'):
            guard.bind_table(EXECUTION, ref)
        guard.completed.add(EXECUTION)
        guard.bind_table(EXECUTION, ref)
        path = '/api/v1/studies/executions/' + EXECUTION + '/response-tables'
        body = {'table': 'actual-table', 'field_ids': ['a'], 'offset': 0, 'page_size': 17}
        with self.assertRaises(ValueError): guard.expect_post(path, {**body, 'offset': 1})
        guard.expect_post(path, body)
        guard.check('POST', cloud.NATIVE + path, body)
        with self.assertRaises(ValueError): guard.expect_post(path, body)

    def test_real_http_transport_boundary_retains_bytes_before_parsing_and_refuses_redirects(self):
        import httpx
        case = self.load()
        for status in (200, 201, 202, 302):
            root = self.root / f'wire-{status}'
            root.mkdir()
            def handler(request):
                self.assertEqual(str(request.url), cloud.NATIVE + '/health')
                return http_reply(status, value={'git_sha': case.native_source})
            with httpx.Client(transport=httpx.MockTransport(handler), headers={'Authorization': 'Bearer opaque-test-only'}) as client:
                wire = cloud.Wire(client, cloud.RouteGuard(case), root)
                if status == 200:
                    self.assertEqual(wire.call('GET', '/health')['git_sha'], case.native_source)
                else:
                    with self.assertRaisesRegex(ValueError, 'HTTP status'):
                        wire.call('GET', '/health')
            retained = list(root.iterdir())
            self.assertEqual(len(retained), 3)
            self.assertFalse(any(b'opaque-test-only' in p.read_bytes() for p in retained))
            self.assertEqual(json.loads((root / 'wire-001.response.json').read_text())['status'], status)

    def test_cloud_launch_uses_reserved_id_and_complete_api_telemetry_without_local_root(self):
        import httpx
        case = self.load()
        source = driver.sources(case)[0]
        nodes = [{'type': 'garch_forecast_from_fit', 'options': {'model_ref': case.model_ref,
            'fit_execution_id': PRODUCER}, 'inputs': {'__model_artifact': {
                'kind': 'literal', 'literal_value': json.dumps(case.digest)}}} for _ in range(4)]
        seen = []
        jobs = []
        sentinel = 'TEST-SIGNED-QUERY-SENTINEL-MUST-NOT-BE-RETAINED'
        def handler(request):
            path = request.url.path
            seen.append((request.method, path))
            if path.endswith('/compile-view'): return http_reply(200, value={'nodes': nodes})
            if path == '/api/v1/studies/definitions': return http_reply(201, value={'id': DEFINITION})
            if path == '/api/v1/studies/executions':
                body = json.loads(request.content)
                self.assertEqual(body, {'id': DEFINITION, 'execution_id': EXECUTION})
                return http_reply(201, value={'id': EXECUTION})
            if path.endswith('/study'):
                return http_reply(200, value={'execution_id': EXECUTION, 'definition_id': DEFINITION,
                    'study': {'source': source}})
            if path.endswith('/logs'):
                return http_reply(200, value={'pagination': {'offset': 0, 'total': 1, 'has_more': False},
                    'events': [{'event_id': 'normal-log', 'context': {}, 'message': 'completed'}]})
            if path.endswith('/stream'):
                return http_reply(200, content=('event: lifecycle\ndata: ' + json.dumps({
                    'operation_type': 'job', 'operation_name': EXECUTION, 'status': 'completed'})
                    + '\n\nevent: status\ndata: {"status":"completed"}\n\n').encode())
            if path.endswith('/model-fits'): return http_reply(200, value={'items': [], 'next_cursor': ''})
            self.assertEqual(path, '/api/v1/studies/executions/' + EXECUTION)
            job = {'id': EXECUTION, 'definition_id': DEFINITION, 'owner_user_id': OWNER,
                   'status': 'completed' if jobs else 'running', 'ordinary_fact': {'preserved': True},
                   'artifact_source': {'kind': 'cloudfront', 'base_url': 'https://cdn.invalid/owned/',
                       'manifest_url': 'https://cdn.invalid/owned/charts/manifest.json',
                       'expires_epoch': 123456, 'signed_query': sentinel}}
            jobs.append(copy.deepcopy(job))
            return http_reply(200, value=job)
        guard = cloud.RouteGuard(case)
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            definition = cloud.launch(cloud.Wire(client, guard, self.root), case, source, EXECUTION,
                self.root / 'consumer', sleep=lambda _: None)
        self.assertEqual(definition, DEFINITION)
        self.assertEqual(sum(method == 'POST' for method, _ in seen), 3)
        self.assertEqual(sum(method == 'GET' for method, _ in seen), 6)
        self.assertTrue((self.root / 'consumer/fit-effects.json').is_file())
        self.assertFalse((self.root / 'consumer/config.json').exists())
        for index, job in enumerate(jobs, 4):
            prefix = self.root / f'wire-{index:03d}'
            metadata = json.loads(prefix.with_suffix('.response.json').read_text())
            wire_bytes = json.dumps(job, separators=(',', ':')).encode()
            job['artifact_source']['signed_query'] = '[REDACTED]'
            retained = prefix.with_suffix('.response.bin').read_bytes()
            self.assertEqual(json.loads(retained), job)
            self.assertEqual(metadata['wire_body_sha256'], hashlib.sha256(wire_bytes).hexdigest())
            self.assertEqual(metadata['retained_body_sha256'], hashlib.sha256(retained).hexdigest())
            self.assertNotEqual(metadata['wire_body_sha256'], metadata['retained_body_sha256'])
            self.assertEqual(metadata['retention'], 'native_execution_metadata_redacted')
        self.assertEqual(json.loads((self.root / 'consumer/terminal-job.json').read_text()), jobs[-1])
        self.assertFalse(any(sentinel.encode() in p.read_bytes() for p in self.root.rglob('*') if p.is_file()))

    def test_wrong_write_success_status_stops_without_replaying_the_write(self):
        import httpx
        case = self.load()
        source = driver.sources(case)[0]
        operations = [('/api/v1/script-validator/compile-view',
                       {'source': source, 'study_assets': [], 'study_type': 'research'}, 200),
                      ('/api/v1/studies/definitions', {'source': source, 'data': {'assets': []},
                       'name': 'Owned GARCH temporary',
                       'description': 'Synthetic owned lifecycle fixture; no vendor inputs.'}, 201),
                      ('/api/v1/studies/executions', {'id': DEFINITION, 'execution_id': EXECUTION}, 201)]
        for index, unexpected in enumerate((201, 200, 202)):
            with self.subTest(operation=index):
                root = self.root / f'wrong-status-{index}'
                root.mkdir()
                guard, forwarded = cloud.RouteGuard(case), []
                def handler(request):
                    forwarded.append(str(request.url))
                    current = len(forwarded) - 1
                    status = unexpected if current == index else operations[current][2]
                    return http_reply(status, value={'id': DEFINITION})
                with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                    wire = cloud.Wire(client, guard, root)
                    for path, body, _ in operations[:index]:
                        wire.post(path, body)
                        if path.endswith('/definitions'):
                            guard.definitions[EXECUTION] = DEFINITION
                    path, body, _ = operations[index]
                    with self.assertRaisesRegex(ValueError, 'HTTP status'):
                        wire.post(path, body)
                    with self.assertRaises(ValueError):
                        wire.post(path, body)
                self.assertEqual(len(forwarded), index + 1)
                self.assertEqual(json.loads((root / f'wire-{index + 1:03d}.response.json').read_text())['status'], unexpected)

    def test_execution_error_malformed_and_truncated_bodies_are_never_retained(self):
        import httpx
        case = self.load()
        sentinel = 'TEST-ERROR-SIGNED-QUERY-SENTINEL'
        class BrokenStream(httpx.SyncByteStream):
            def __iter__(self):
                yield ('{"artifact_source":{"signed_query":"' + sentinel + '"}').encode()
                raise httpx.ReadError('Synthetic truncated execution metadata')
        replies = [lambda: http_reply(503, value={'artifact_source': {'signed_query': sentinel}}),
                   lambda: http_reply(200, content=('{"artifact_source":{"signed_query":"' + sentinel + '"}').encode()),
                   lambda: http_reply(200, value={'id': EXECUTION, 'status': 'completed', 'artifact_source': sentinel}),
                   lambda: httpx.Response(200, stream=BrokenStream())]
        for index, reply in enumerate(replies):
            root = self.root / f'invalid-execution-{index}'
            root.mkdir()
            with httpx.Client(transport=httpx.MockTransport(lambda request: reply())) as client:
                wire = cloud.Wire(client, cloud.RouteGuard(case), root)
                with self.subTest(index=index), self.assertRaises(Exception):
                    wire.call('GET', '/api/v1/studies/executions/' + EXECUTION)
            self.assertFalse(list(root.glob('*.response.bin')))
            metadata = json.loads((root / 'wire-001.response.json').read_text())
            self.assertTrue(metadata['body_omitted'])
            self.assertEqual(metadata['retention'], 'execution_metadata_body_omitted')
            self.assertFalse(any(sentinel.encode() in p.read_bytes() for p in root.iterdir()))

    def test_execution_gzip_wire_hash_and_retained_redacted_hash_are_distinct(self):
        import httpx
        case = self.load()
        job = {'id': EXECUTION, 'status': 'running', 'definition_id': DEFINITION,
               'artifact_source': {'kind': 'cloudfront', 'signed_query': 'TEST-GZIP-GRANT-SENTINEL'}}
        raw = json.dumps(job, separators=(',', ':')).encode()
        encoded = gzip.compress(raw)
        with httpx.Client(transport=httpx.MockTransport(lambda request: http_reply(
                200, content=encoded, headers={'content-encoding': 'gzip'}))) as client:
            actual = cloud.Wire(client, cloud.RouteGuard(case), self.root).call(
                'GET', '/api/v1/studies/executions/' + EXECUTION)
        job['artifact_source']['signed_query'] = '[REDACTED]'
        self.assertEqual(actual, job)
        metadata = json.loads((self.root / 'wire-001.response.json').read_text())
        self.assertEqual(metadata['wire_body_sha256'], hashlib.sha256(encoded).hexdigest())
        retained = (self.root / 'wire-001.response.bin').read_bytes()
        self.assertEqual(metadata['retained_body_sha256'], hashlib.sha256(retained).hexdigest())
        self.assertEqual(json.loads(retained), job)
        self.assertFalse(any(b'TEST-GZIP-GRANT-SENTINEL' in p.read_bytes() for p in self.root.rglob('*') if p.is_file()))

    def test_non_execution_payloads_are_unchanged_by_narrow_grant_policy(self):
        import httpx
        case = self.load()
        raw = b'{"artifact_source":{"signed_query":"ordinary-model-payload"},"fact":42}'
        path = '/api/v1/studies/executions/' + PRODUCER + '/model-fits/owned'
        with httpx.Client(transport=httpx.MockTransport(lambda request: http_reply(200, content=raw))) as client:
            actual = cloud.Wire(client, cloud.RouteGuard(case), self.root).call('GET', path)
        self.assertEqual(actual, json.loads(raw))
        self.assertEqual((self.root / 'wire-001.response.bin').read_bytes(), raw)
        metadata = json.loads((self.root / 'wire-001.response.json').read_text())
        self.assertEqual(metadata['wire_body_sha256'], metadata['retained_body_sha256'])
        self.assertEqual(metadata['retention'], 'unmodified_application_body')

    def test_binary_response_table_bytes_remain_unchanged_with_wire_encoding(self):
        import httpx
        case = self.load()
        root = self.root / 'binary'
        root.mkdir()
        guard = cloud.RouteGuard(case)
        guard.completed.add(EXECUTION)
        ref = {'table': 'bound-table', 'field_ids': ['forecast'], 'row_count': 4}
        guard.bind_table(EXECUTION, ref)
        raw = b'BINARY-TRANSPORT-CONTROL\x00\xff\x00'
        encoded = gzip.compress(raw)
        body = {'table': ref['table'], 'field_ids': ref['field_ids'], 'offset': 0, 'page_size': 17}
        with httpx.Client(transport=httpx.MockTransport(lambda request: http_reply(
                200, content=encoded, headers={'content-encoding': 'gzip'}))) as client:
            actual = cloud.Wire(client, guard, root).post(
                '/api/v1/studies/executions/' + EXECUTION + '/response-tables', body, binary=True)
        self.assertEqual(actual, raw)
        self.assertEqual((root / 'wire-001.response.bin').read_bytes(), raw)
        metadata = json.loads((root / 'wire-001.response.json').read_text())
        self.assertEqual(metadata['wire_body_sha256'], hashlib.sha256(encoded).hexdigest())
        self.assertEqual(metadata['retained_body_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(metadata['retention'], 'unmodified_application_body')

    def test_unknown_write_outcome_cannot_repeat_the_consumed_write(self):
        import httpx
        case = self.load()
        guard = cloud.RouteGuard(case)
        source = driver.sources(case)[0]
        body = {'source': source, 'study_assets': [], 'study_type': 'research'}
        attempts = []
        def failed(request):
            attempts.append(str(request.url))
            raise httpx.ReadTimeout('Deliberately unknown fixture outcome')
        with httpx.Client(transport=httpx.MockTransport(failed)) as client:
            wire = cloud.Wire(client, guard, self.root)
            with self.assertRaises(httpx.ReadTimeout): wire.post('/api/v1/script-validator/compile-view', body)
            with self.assertRaises(ValueError): wire.post('/api/v1/script-validator/compile-view', body)
        self.assertEqual(len(attempts), 1)
        self.assertTrue((self.root / 'wire-001.request.json').is_file())

    def test_fit_node_in_compile_projection_stops_before_definition_or_execution(self):
        case = self.load()
        class CompileOnly:
            calls = []
            def post(self, path, body):
                self.calls.append(path)
                return {'nodes': [{'type': 'garch_forecast_from_fit'}] * 4 + [{'type': 'garch_fit'}]}
        wire = CompileOnly()
        with self.assertRaisesRegex(AssertionError, 'zero-fit consumer shape'):
            cloud.launch(wire, case, driver.sources(case)[0], EXECUTION, self.root / 'invalid-fit')
        self.assertEqual(wire.calls, ['/api/v1/script-validator/compile-view'])

    def test_missing_duplicate_or_fit_logs_cannot_prove_zero_fits(self):
        variants = [[], [{'event_id': 'duplicate'}, {'event_id': 'duplicate'}],
                    [{'event_id': 'fit', 'context': {'model_operation': 'fit'}}]]
        for index, events in enumerate(variants):
            class LogsOnly:
                def call(self, method, path, **kwargs):
                    self_path = '/api/v1/studies/executions/' + EXECUTION + '/logs'
                    if path != self_path:
                        raise AssertionError('No subsequent request after invalid log evidence')
                    return {'events': events, 'pagination': {'total': len(events), 'offset': 0, 'has_more': False}}
            stage = self.root / f'bad-logs-{index}'
            stage.mkdir()
            with self.subTest(index=index), self.assertRaises(AssertionError):
                cloud.read_effects(LogsOnly(), EXECUTION, stage)

    def test_missing_credential_and_spent_stage_never_import_or_resubmit_sdk(self):
        case = self.load()
        with patch.object(cloud, 'runtime_dependencies'), self.assertRaisesRegex(ValueError, 'no opaque credential'):
            driver.run(case, self.root / 'attempt', lambda: None)
        self.assertTrue((self.root / 'attempt' / 'STOP.json').is_file())
        with patch.object(cloud, 'runtime_dependencies'), self.assertRaises(FileExistsError):
            driver.run(case, self.root / 'different-output', lambda: self.fail('No refresh on spent admission'))

    def test_unprepared_runtime_refuses_before_credential_fence_or_requests(self):
        case = self.load()
        with patch.object(cloud, 'runtime_dependencies', side_effect=ImportError('missing Arrow')):
            with self.assertRaises(ImportError):
                driver.run(case, self.root / 'unprepared', lambda: self.fail('No credential call'))
        self.assertFalse((self.root / 'unprepared').exists())
        self.assertFalse(case.cloud.path.with_suffix('.json.spent').exists())

    def prepare_capture(self):
        case = self.load()
        evidence = self.root / 'pending'
        evidence.mkdir()
        nodes = []
        for h in (1, 2, 5, 21):
            seal = {'role': 'Consumer', 'owner_user_id': OWNER, 'family': 'GARCH', 'model_ref': 'owned',
                'resolved_binding': {'digest': case.digest, 'fit_cutoff': case.fit_cutoff},
                'source_execution_id': PRODUCER, 'binding_kind': 'ExecutionFit', 'capability': 'ForecastCapable',
                'as_of': case.fit_cutoff, 'forecast': {'origin_policy': 'FitCheckpoint',
                    'origin_input_id': '__model_artifact', 'horizon_unit': 'InputObservations',
                    'target_policy': 'OriginPlusHorizon', 'horizon_count': h}}
            nodes.append({'id': f'f{h}', 'type': 'garch_forecast_from_fit', 'model_output_use': 'ObservedCausal',
                'runtime_contract_json': json.dumps({'modelOutputUse': 'ObservedCausal', 'modelCapabilities': ['ForecastCapable']}),
                'model_artifact_contract_json': json.dumps(seal)})
        config = self.ref('actual-config.json', {'execution_plan': {'nodes': nodes}})
        pending = {'status': 'PENDING_ACTUAL_SEALED_CONFIG_CAPTURE', 'admission_sha256': case.admission_sha256,
            'case_id': CASE, 'owner_user_id': OWNER, 'family': 'GARCH', 'stage': 'temporary', 'producer': PRODUCER,
            'digest': case.digest, 'model_ref': 'owned', 'manifest_sha256': None,
            'executions': [EXECUTION], 'definitions': [DEFINITION], 'fit_operations_per_consumer': [0]}
        pending_ref = self.ref('pending/pending-receipt.json', pending)
        identity = {'owner_user_id': OWNER, 'execution_id': EXECUTION, 'definition_id': DEFINITION,
            'status': 'Completed', 'config_sha256': config['sha256'], 'source_sha256': case.cloud.source_sha256[0],
            'completed_generation': 'actual-generation'}
        receipt = self.ref('read-receipt.json', {**identity, 'root_admitted': True})
        capture = {'kind': 'ROOT_ADMITTED_DEV_MODEL_LIFECYCLE_CONFIG_CAPTURE_V1', 'ready': True,
            'case_id': CASE, 'admission_sha256': case.admission_sha256, 'root_packet_sha256': case.root_packet_sha256,
            'pending_receipt': pending_ref, 'consumers': [{**identity, 'config': config, 'capture_read_receipt': receipt}]}
        return case, evidence, capture

    def test_offline_finalization_requires_actual_bound_contract_and_preserves_pending(self):
        case, evidence, capture = self.prepare_capture()
        before = (evidence / 'pending-receipt.json').read_bytes()
        ref = self.ref('capture.json', capture)
        cloud.finalize(case, evidence, Path(ref['path']), ref['sha256'])
        result = json.loads((evidence / 'receipt.json').read_text())
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['contract_witnesses'][0]['horizons'], [1, 2, 5, 21])
        self.assertEqual((evidence / 'pending-receipt.json').read_bytes(), before)
        with self.assertRaises(FileExistsError): cloud.finalize(case, evidence, Path(ref['path']), ref['sha256'])

    def test_wrong_owner_definition_generation_or_config_capture_stays_pending(self):
        case, evidence, capture = self.prepare_capture()
        for key, value in [('owner_user_id', 'different'), ('definition_id', EXECUTION),
                           ('completed_generation', ''), ('config_sha256', '0' * 64)]:
            changed = copy.deepcopy(capture)
            changed['consumers'][0][key] = value
            ref = self.ref('capture.json', changed)
            with self.subTest(key=key), self.assertRaises(ValueError):
                cloud.finalize(case, evidence, Path(ref['path']), ref['sha256'])
            self.assertFalse((evidence / 'receipt.json').exists())
        Path(capture['consumers'][0]['config']['path']).write_text('{}')
        ref = self.ref('capture.json', capture)
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            cloud.finalize(case, evidence, Path(ref['path']), ref['sha256'])


class StageBoundsTest(unittest.TestCase):
    def test_hmm_two_one_one_and_forecast_one_one_one_bounds(self):
        for family in ('HMM', 'ARIMA', 'GARCH'):
            plans = [cloud.stage_plan(family, stage) for stage in ('temporary', 'saved', 'post_cleanup')]
            self.assertEqual(sum(p['consumer_executions'] for p in plans), 4 if family == 'HMM' else 3)
            for plan in plans:
                n = plan['consumer_executions']
                self.assertEqual(plan['GET_cap'], 5 + 85 * n)
                self.assertEqual(plan['Arrow_read_POSTs'], n * (3 if family == 'HMM' else 1))
                self.assertEqual(plan['semantic_fits'], 0)

    def test_producer_plan_is_unadmitted_with_one_original_recipe_fit_and_exact_reads(self):
        for family in ('HMM', 'ARIMA', 'GARCH'):
            plan = cloud.producer_request_plan(family, EXECUTION, 'owned_' + family.lower())
            self.assertEqual(plan['status'], 'SOURCE_PLAN_NOT_ADMITTED')
            self.assertEqual(plan['semantic_fit_cap'], 1)
            self.assertEqual(sum(plan['reads'].values()), plan['GET_cap'])
            self.assertEqual(len(plan['writes']), 3)
            source = plan['writes'][0]['json']['source']
            self.assertEqual(source.count(f'{family.lower()}_fit('), 1)
            self.assertIn('random_state=42', source)
            self.assertEqual(hashlib.sha256(source.encode()).hexdigest(), plan['source_sha256'])
            self.assertEqual(plan['writes'][2]['json']['execution_id'], EXECUTION)

    def test_incomplete_or_other_execution_lifecycle_cannot_prove_zero_fits(self):
        good = ('event: lifecycle\ndata: ' + json.dumps({'operation_type': 'job',
            'operation_name': EXECUTION, 'status': 'completed'}) + '\n\n'
            + 'event: status\ndata: {"status":"completed"}\n\n').encode()
        self.assertEqual(len(cloud.parse_lifecycle(good, EXECUTION)), 2)
        for raw in (b'', good + good, good.replace(EXECUTION.encode(), PRODUCER.encode()), good.split(b'event: status')[0]):
            with self.assertRaises(AssertionError): cloud.parse_lifecycle(raw, EXECUTION)


if __name__ == '__main__': unittest.main()
