"""Bounded DEV-only consumer transport and offline sealed-contract finalization.

No producer, Save, archive export, S3, restart or cleanup implementation lives
here. API results alone remain pending until root supplies an actual config
capture. Imports are credential/network free; loopback remains the driver default.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import quote, urlencode, urlsplit
from uuid import UUID

NATIVE = 'https://api.dev.epoch.trade'
PLATFORM = 'https://services.dev.epoch.trade'
V6_SHA256 = 'c99882765cc724c111809ad6cfa7cff109fc1da7d55d90986b17729db7068d28'
SOURCE_FILES = ('family_cloud.py', 'family_consumers.py', 'families.py',
                'family_forecasts.py', 'forecast_oracle.py', 'native_artifact.py')


def canonical_origins(native: str, platform: str) -> tuple[str, str]:
    if native != NATIVE or platform != PLATFORM:
        raise ValueError('DEV cloud mode requires the exact canonical api.dev/services.dev HTTPS origins')
    return native, platform


def stage_plan(family: str, stage: str) -> dict:
    if family not in {'HMM', 'ARIMA', 'GARCH'} or stage not in {'temporary', 'saved', 'post_cleanup'}:
        raise ValueError('Unknown bounded family consumer stage')
    runs = 2 if family == 'HMM' and stage == 'temporary' else 1
    return {'consumer_executions': runs, 'compile_POSTs': runs, 'definition_POSTs': runs,
            'execute_POSTs': runs, 'Arrow_read_POSTs': runs * (3 if family == 'HMM' else 1),
            'GET_cap': 5 + 85 * runs, 'poll_GETs_per_execution': 60,
            'log_pages_per_execution': 20, 'semantic_fits': 0, 'Save_POSTs': 0,
            'provider_vendor_calls': 0, 'archive_S3_cleanup_restart_calls': 0,
            'HTTP_retries': 0, 'sealed_contract': 'pending_root_capture'}


def source_hashes() -> dict[str, str]:
    hashes = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
              for name in SOURCE_FILES}
    hashes['tools/executions.py'] = hashlib.sha256(
        (Path(__file__).parent.parent / 'executions.py').read_bytes()).hexdigest()
    return hashes


def producer_request_plan(family: str, execution: str, fit_name: str) -> dict:
    """Prepare a root-reserved producer plan; this module cannot execute it."""
    from .families import family_source
    _uuid(execution)
    if family not in {'HMM', 'ARIMA', 'GARCH'}:
        raise ValueError('Unknown v6 family')
    source = family_source(family, model_ref=fit_name, fit=True)
    return {'status': 'SOURCE_PLAN_NOT_ADMITTED', 'family': family, 'execution_id': execution,
        'fit_name': fit_name, 'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'semantic_fit_cap': 1, 'consumer_execution_cap': 0, 'Save_POSTs': 0,
        'GET_cap': 89, 'Arrow_read_POSTs': 3, 'HTTP_retries': 0, 'provider_vendor_calls': 0,
        'writes': [
            {'method': 'POST', 'url': NATIVE + '/api/v1/script-validator/compile-view',
             'json': {'source': source, 'study_assets': [], 'study_from': '2022-01-01',
                      'study_to': '2025-01-31', 'study_type': 'research'}},
            {'method': 'POST', 'url': NATIVE + '/api/v1/studies/definitions',
             'json': {'source': source, 'data': {'assets': []}, 'name': 'Owned ' + family + ' producer',
                      'description': 'Synthetic owned lifecycle fixture; no vendor inputs.'}},
            {'method': 'POST', 'url': NATIVE + '/api/v1/studies/executions',
             'json': {'id': '__ACTUAL_SERVER_DEFINITION_ID__', 'execution_id': execution,
                      'period': {'start': '2022-01-01', 'end': '2025-01-31'}}}],
        'reads': {'health': 1, 'inventory_before_after': 2, 'terminal_polls': 60,
                  'log_pages_limit100': 20, 'historical_lifecycle': 1, 'fit_roster': 1,
                  'exact_fit': 1, 'outputs': 1, 'response': 1, 'as_run_study': 1},
        'pending_root_gate': 'Actual generation/descriptor/artifact/checkpoint capture is separately admitted; no archive or S3 fallback'}


@dataclass(frozen=True)
class CloudAdmission:
    path: Path
    case_id: str
    executions: tuple[str, ...]
    source_sha256: tuple[str, ...]
    platform_source: str
    producer_definition: str


def _uuid(value: str) -> str:
    if str(UUID(value)) != value:
        raise ValueError('Use canonical root-reserved UUIDs')
    return value


def admit_cloud(value: dict, runtime: dict, path: Path, reference) -> CloudAdmission:
    canonical_origins(value['native_url'], value['platform_url'])
    if value.get('native_root') is not None or runtime.get('native_root') is not None:
        raise ValueError('Cloud admission cannot masquerade as a local runtime storage root')
    if value['storage_location'] != 'shared_personal_v1':
        raise ValueError('Cloud cases require a genuine completed shared producer')
    if (not re.fullmatch('[a-f0-9]{40}', value['platform_source'])
            or runtime.get('platform_source') != value['platform_source']):
        raise ValueError('Actual Platform source is not pinned to runtime admission')
    for name in ('native', 'platform'):
        reference(runtime['runtime_receipts'][name])
    _, plan_sha = reference(value['acceptance_plan'])
    if plan_sha != V6_SHA256 or value['harness_sources'] != source_hashes():
        raise ValueError('Frozen v6 bounds or admitted harness source changed')
    generation_body, _ = reference(value['completed_generation_witness'])
    generation = json.loads(generation_body)
    descriptor_body, _ = reference(value['producer_descriptor'])
    descriptor = json.loads(descriptor_body)
    definition = _uuid(value['producer_definition_id'])
    if (generation.get('root_admitted') is not True or generation.get('status') != 'Completed'
            or generation.get('producer_config_sha256') != value['producer_config_sha256']
            or generation.get('definition_id') != definition or descriptor.get('definition_id') != definition):
        raise ValueError('A root-pinned actual completed generation is required')
    if value['stage'] == 'post_cleanup':
        cleanup_body, _ = reference(value['supported_cleanup_witness'])
        cleanup = json.loads(cleanup_body)
        if (cleanup.get('source_execution_removed') is not True
                or cleanup.get('temporary_artifact_removed') is not True):
            raise ValueError('Generation deletion alone cannot prove temporary-artifact cleanup')
        reference(cleanup['supported_operation_receipt'])
    count = stage_plan(value['family'], value['stage'])['consumer_executions']
    ids = tuple(_uuid(item) for item in value['consumer_execution_ids'])
    if (len(ids) != count or len(set(ids)) != count
            or value['producer_execution_id'] in ids
            or value['request_plan'] != stage_plan(value['family'], value['stage'])):
        raise ValueError('Cloud consumer identities or v6 stage request caps differ')
    _uuid(value['owner_user_id'])
    _uuid(value['producer_execution_id'])
    hashes = tuple(value['consumer_source_sha256'])
    if len(hashes) != count or any(not re.fullmatch('[a-f0-9]{64}', item) for item in hashes):
        raise ValueError('Every root-reserved consumer needs its exact source hash')
    return CloudAdmission(path, _uuid(value['case_id']), ids, hashes, value['platform_source'], definition)


def durable(path: Path, value: object) -> None:
    raw = json.dumps(value, indent=2, allow_nan=False).encode() + b'\n'
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb') as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())


class RouteGuard:
    """Closed per-stage routes; a write/read POST needs an exact one-use body."""
    def __init__(self, case):
        from .family_consumers import sources
        self.case, self.sources = case, sources(case)
        self.write_index = 0
        self.definitions: dict[str, str] = {}
        self.completed: set[str] = set()
        self.tables: list[tuple[str, dict]] = []
        self.table_bindings: set[str] = set()
        self.counts: dict[tuple, int] = {}
        self.caps = {('GET', NATIVE, '/health'): 1,
                     ('GET', NATIVE, '/api/v1/studies/executions'): 2}
        selection = ('/api/v1/models/' + case.model_ref if case.manifest_sha256 else
                     '/api/v1/studies/executions/' + case.producer + '/model-fits/' + quote(case.fit_name, safe=''))
        self.caps[('GET', PLATFORM if case.manifest_sha256 else NATIVE, selection)] = 2
        for execution in case.cloud.executions:
            root = '/api/v1/studies/executions/' + execution
            for suffix, cap in [('', 60), ('/logs', 20), ('/stream', 1), ('/model-fits', 1),
                                ('/outputs', 1), ('/study', 1)]:
                self.caps[('GET', NATIVE, root + suffix)] = cap
        self.next_post: tuple | None = None

    def expect_post(self, path: str, body: dict) -> None:
        if self.next_post is not None:
            raise ValueError('A pending write cannot be replaced')
        if path.endswith('/response-tables'):
            if not self.tables or (path, body) != self.tables[0]:
                raise ValueError('Unbound, repeated or changed Arrow read')
            self.tables.pop(0)
        else:
            index, operation = divmod(self.write_index, 3)
            if index >= len(self.sources):
                raise ValueError('Consumer write cap exhausted')
            source, execution = self.sources[index], self.case.cloud.executions[index]
            if operation == 0:
                expected_path = '/api/v1/script-validator/compile-view'
                expected_body = {'source': source, 'study_assets': [], 'study_type': 'research'}
                if self.case.family == 'HMM':
                    expected_body.update(study_from='2022-01-01', study_to='2025-01-31')
            elif operation == 1:
                expected_path = '/api/v1/studies/definitions'
                expected_body = {'source': source, 'data': {'assets': []},
                    'name': 'Owned ' + self.case.family + ' ' + self.case.stage,
                    'description': 'Synthetic owned lifecycle fixture; no vendor inputs.'}
            else:
                expected_path = '/api/v1/studies/executions'
                if execution not in self.definitions:
                    raise ValueError('Missing actual server definition identity')
                expected_body = {'id': self.definitions[execution], 'execution_id': execution}
                if self.case.family == 'HMM':
                    expected_body['period'] = {'start': '2022-01-01', 'end': '2025-01-31'}
            if (path, body) != (expected_path, expected_body):
                raise ValueError('Write path/body/order differs from frozen consumer intent')
            self.write_index += 1
        self.next_post = ('POST', NATIVE, path, body)

    def bind_table(self, execution: str, ref: dict) -> None:
        if execution not in self.completed or self.tables or execution in self.table_bindings:
            raise ValueError('Arrow reads require the actual completed execution')
        self.table_bindings.add(execution)
        self.tables = [('/api/v1/studies/executions/' + execution + '/response-tables',
            {'table': ref['table'], 'field_ids': ref['field_ids'], 'offset': offset, 'page_size': 17})
            for offset in range(0, ref['row_count'], 17)]

    def bind_response(self, response: str) -> None:
        if not isinstance(response, str) or not re.fullmatch('[A-Za-z0-9_.:-]{1,256}', response):
            raise ValueError('Invalid actual response identity')
        self.caps[('GET', NATIVE, '/api/v1/research/responses/' + response)] = 1

    def check(self, method: str, url: str, body: dict | None = None) -> None:
        parsed = urlsplit(url)
        origin = f'{parsed.scheme}://{parsed.netloc}'
        if (origin not in {NATIVE, PLATFORM} or parsed.fragment or parsed.username
                or parsed.path != quote(parsed.path, safe='/-._~:')):
            raise ValueError('Unadmitted origin or encoded path')
        if method == 'POST':
            if parsed.query or self.next_post != (method, origin, parsed.path, body):
                raise ValueError('Unadmitted or repeated POST/body')
            self.next_post = None  # Consumed BEFORE forwarding, including unknown outcomes.
        elif method == 'GET':
            from urllib.parse import parse_qs
            query = parse_qs(parsed.query, keep_blank_values=True)
            for execution in self.case.cloud.executions:
                root = '/api/v1/studies/executions/' + execution
                if parsed.path in {root + suffix for suffix in ('/logs', '/stream', '/model-fits', '/outputs', '/study')}:
                    if execution not in self.completed:
                        raise ValueError('Completion evidence/results cannot be read before actual completion')
            if parsed.path.endswith('/logs'):
                if (set(query) != {'limit', 'offset'} or query['limit'] != ['100']
                        or len(query['offset']) != 1 or not query['offset'][0].isdigit()):
                    raise ValueError('Only bounded log pages are admitted')
            elif parsed.path.endswith('/stream'):
                if query != {'include_historical': ['true'], 'types': ['lifecycle']}:
                    raise ValueError('Only finite historical lifecycle reads are admitted')
            elif parsed.path.endswith('/model-fits'):
                if query != {'limit': ['2']}:
                    raise ValueError('Only the empty-fit roster check is admitted')
            elif query:
                raise ValueError('Unadmitted read query')
        key = (method, origin, parsed.path)
        if method != 'POST' and self.counts.get(key, 0) >= self.caps.get(key, 0):
            raise ValueError('Unadmitted route/method or exhausted read cap')
        self.counts[key] = self.counts.get(key, 0) + 1


class Wire:
    def __init__(self, client, guard: RouteGuard, root: Path):
        self.client, self.guard, self.root, self.sequence = client, guard, root, 0

    def call(self, method: str, path: str, *, origin: str = NATIVE, body=None, query=None, binary=False):
        url = origin + path + ('?' + urlencode(query) if query else '')
        self.guard.check(method, url, body)
        self.sequence += 1
        prefix = self.root / f'wire-{self.sequence:03d}'
        durable(prefix.with_suffix('.request.json'), {'method': method, 'url': url, 'json': body})
        # No credential headers are retained. Redirects and automatic retries are disabled.
        execution_record = (method == 'GET' and origin == NATIVE and path in {
            '/api/v1/studies/executions/' + execution for execution in self.guard.case.cloud.executions})
        with self.client.stream(method, url, json=body) as response:
            chunks, count, deadline = [], 0, time.monotonic() + 45
            wire_hash = hashlib.sha256()
            try:
                for part in response.iter_raw():
                    count += len(part)
                    wire_hash.update(part)
                    if count > 8 * 1024 * 1024 or time.monotonic() > deadline:
                        raise ValueError('Response exceeded bounded evidence size/time')
                    chunks.append(part)
            except BaseException as error:
                if execution_record:
                    durable(prefix.with_suffix('.response.json'), {'status': response.status_code,
                        'wire_body_complete': False, 'received_body_bytes': count,
                        'received_body_sha256': wire_hash.hexdigest(), 'body_omitted': True,
                        'retention': 'execution_metadata_body_omitted', 'error_type': type(error).__name__})
                raise
            raw = b''.join(chunks)
            metadata = {'status': response.status_code, 'wire_body_bytes': len(raw),
                        'wire_body_sha256': wire_hash.hexdigest(), 'wire_body_complete': True,
                        'content_encoding': response.headers.get('content-encoding', 'identity')}
            retained = raw
            value = None
            try:
                # Preserve actual encoded wire hashes independently of decoded application bytes.
                if metadata['content_encoding'] != 'identity':
                    import httpx
                    decoded, decoded_count = [], 0
                    local = httpx.Response(response.status_code,
                        headers={'content-encoding': metadata['content_encoding']},
                        stream=httpx.ByteStream(raw))
                    try:
                        for part in local.iter_bytes(chunk_size=65536):
                            decoded_count += len(part)
                            if decoded_count > 8 * 1024 * 1024:
                                raise ValueError('Decoded response exceeded evidence size bound')
                            decoded.append(part)
                    finally:
                        local.close()
                    retained = b''.join(decoded)
                if execution_record:
                    # Only the successful Native execution DTO has this narrow grant policy.
                    from tools.executions import redact_execution_metadata
                    if response.status_code != 200:
                        raise ValueError('Non-200 Native execution metadata')
                    value = redact_execution_metadata(json.loads(retained))
                    retained = json.dumps(value, indent=2, allow_nan=False).encode() + b'\n'
                    metadata['retention'] = 'native_execution_metadata_redacted'
                else:
                    metadata['retention'] = 'unmodified_application_body'
            except Exception:
                if execution_record:
                    durable(prefix.with_suffix('.response.json'), {**metadata,
                        'retention': 'execution_metadata_body_omitted', 'body_omitted': True})
                raise
            with os.fdopen(os.open(prefix.with_suffix('.response.bin'), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb') as output:
                output.write(retained); output.flush(); os.fsync(output.fileno())
            durable(prefix.with_suffix('.response.json'), {**metadata,
                'retained_body_bytes': len(retained), 'retained_body_sha256': hashlib.sha256(retained).hexdigest()})
            if response.status_code != 200:
                raise ValueError('Non-200 response stops this attempt; no retries')
        return retained if binary else value if execution_record else json.loads(retained)

    def post(self, path: str, body: dict, *, binary=False):
        self.guard.expect_post(path, body)
        return self.call('POST', path, body=body, binary=binary)


def parse_lifecycle(raw: bytes, execution: str) -> list[dict]:
    frames = []
    for block in raw.decode().replace('\r\n', '\n').split('\n\n'):
        event, data = '', []
        for line in block.splitlines():
            if line.startswith('event:'): event = line[6:].strip()
            if line.startswith('data:'): data.append(line[5:].strip())
        if data:
            frames.append({'event': event, 'data': json.loads('\n'.join(data))})
    terminal = [f['data'] for f in frames if f['event'] == 'lifecycle'
                and f['data'].get('operation_type') == 'job'
                and f['data'].get('operation_name') == execution
                and f['data'].get('status') in {'completed', 'failed', 'cancelled'}]
    if (len(terminal) != 1 or terminal[0]['status'] != 'completed'
            or not any(f['event'] == 'status' and f['data'].get('status') == 'completed' for f in frames)):
        raise AssertionError('Missing unique actual completed lifecycle/status evidence')
    return frames


def read_effects(wire: Wire, execution: str, stage: Path) -> None:
    root, logs, total = '/api/v1/studies/executions/' + execution, [], None
    for _ in range(20):
        page = wire.call('GET', root + '/logs', query={'limit': 100, 'offset': len(logs)})
        pagination = page['pagination']
        total = pagination['total'] if total is None else total
        if (type(total) is not int or not 0 < total <= 2000
                or pagination['total'] != total or pagination['offset'] != len(logs)
                or len(page['events']) > 100):
            raise AssertionError('Log pagination changed or skipped rows')
        logs.extend(page['events'])
        if not pagination['has_more']:
            if not logs or len(logs) != total:
                raise AssertionError('Missing/truncated telemetry cannot prove zero fits')
            break
        if not page['events']:
            raise AssertionError('Log pagination made no progress')
    else:
        raise ValueError('Log page cap reached; no further requests')
    durable(stage / 'logs-complete.json', logs)
    ids = [event.get('event_id') for event in logs]
    if any(not isinstance(item, str) or not item for item in ids) or len(set(ids)) != len(ids):
        raise AssertionError('Missing/duplicated log identities cannot prove a complete zero-fit stream')
    if any(e.get('context', {}).get('model_operation') == 'fit' for e in logs):
        raise AssertionError('Consumer performed an unexpected fit')
    frames = parse_lifecycle(wire.call('GET', root + '/stream',
        query={'include_historical': 'true', 'types': 'lifecycle'}, binary=True), execution)
    durable(stage / 'historical-lifecycle.json', frames)
    roster = wire.call('GET', root + '/model-fits', query={'limit': 2})
    if roster['items'] or roster.get('next_cursor'):
        raise AssertionError('Consumer produced a reusable fit')
    durable(stage / 'fit-effects.json', {'started': 0, 'completed': 0,
        'canonical_stream': 'complete owned logs API', 'unique_completed_lifecycle': True,
        'empty_fit_roster': roster})


def launch(wire: Wire, case, source: str, execution: str, stage: Path, *, sleep=time.sleep):
    stage.mkdir(mode=0o700)
    (stage / 'source.epoch').write_text(source)
    compile_body = {'source': source, 'study_assets': [], 'study_type': 'research'}
    if case.family == 'HMM':
        compile_body.update(study_from='2022-01-01', study_to='2025-01-31')
    compiled = wire.post('/api/v1/script-validator/compile-view', compile_body)
    nodes = compiled['nodes']
    selected = [n for n in nodes if n['type'] == case.family.lower() + ('_infer' if case.family == 'HMM' else '_forecast_from_fit')]
    if (len(selected) != (1 if case.family == 'HMM' else 4)
            or any((n['type'].endswith('_fit') and not n['type'].endswith('_forecast_from_fit'))
                   or n['type'].startswith('llm') for n in nodes)):
        raise AssertionError('Compile projection changed the zero-fit consumer shape')
    for node in selected:
        options = node['options']
        if (options.get('model_ref') != case.model_ref
                or options.get('model_manifest_sha256', '') != (case.manifest_sha256 or '')
                or options.get('fit_execution_id', '') != ('' if case.manifest_sha256 else case.producer)
                or node['inputs']['__model_artifact'].get('kind') != 'literal'
                or json.loads(node['inputs']['__model_artifact']['literal_value']) != case.digest):
            raise AssertionError('Compile projection changed the exact selected binding')
    durable(stage / 'compiled-projection.json', compiled)  # Not a sealed-config proof.
    created = wire.post('/api/v1/studies/definitions', {'source': source, 'data': {'assets': []},
        'name': 'Owned ' + case.family + ' ' + case.stage,
        'description': 'Synthetic owned lifecycle fixture; no vendor inputs.'})
    definition = _uuid(created['id'])
    wire.guard.definitions[execution] = definition
    body = {'id': definition, 'execution_id': execution}
    if case.family == 'HMM':
        body['period'] = {'start': '2022-01-01', 'end': '2025-01-31'}
    started = wire.post('/api/v1/studies/executions', body)
    if started['id'] != execution:
        raise AssertionError('Reserved execution identity changed')
    root = '/api/v1/studies/executions/' + execution
    for _ in range(60):
        job = wire.call('GET', root)
        if job['id'] != execution or job['definition_id'] != definition:
            raise AssertionError('Actual job identity changed')
        status = job['status'].lower()
        if status in {'completed', 'failed', 'cancelled'}:
            from tools.executions import redact_execution_metadata
            durable(stage / 'terminal-job.json', redact_execution_metadata(job))
            if status != 'completed': raise AssertionError('Consumer did not complete')
            wire.guard.completed.add(execution)
            break
        sleep(5)
    else:
        raise TimeoutError('Completion cap reached; STOP without resubmission')
    snapshot = wire.call('GET', root + '/study')
    if (snapshot['execution_id'] != execution or snapshot['definition_id'] != definition
            or snapshot['study']['source'] != source):
        raise AssertionError('Actual as-run source differs from admission')
    durable(stage / 'as-run-study.json', snapshot)
    read_effects(wire, execution, stage)
    return definition


def read_answer(wire: Wire, case, execution: str) -> dict:
    import pyarrow as pa
    import pyarrow.ipc as ipc
    root = '/api/v1/studies/executions/' + execution
    output = wire.call('GET', root + '/outputs')
    if output['execution_id'] != execution or type(output['response_version']) is not int or output['response_version'] < 1:
        raise AssertionError('Output identity/version changed')
    wire.guard.bind_response(output['response_id'])
    response = wire.call('GET', '/api/v1/research/responses/' + output['response_id'])
    if (response['response_id'] != output['response_id'] or response['request_id'] != output['request_id']
            or response['version'] != output['response_version'] or response['primary_execution_id'] != execution):
        raise AssertionError('Answer identity/version/request differs from the accepted output')
    parts = response['content']['outputs']
    if len(parts) != 1 or parts[0]['name'] != ('observed' if case.family == 'HMM' else 'forecasts'):
        raise AssertionError('Answer output roster changed')
    ref = parts[0]['source']
    if ref['execution_id'] != execution or ref['row_count'] != (40 if case.family == 'HMM' else 4):
        raise AssertionError('Answer execution or complete row count changed')
    wire.guard.bind_table(execution, ref)
    rows = []
    for offset in range(0, ref['row_count'], 17):
        query = {'table': ref['table'], 'field_ids': ref['field_ids'], 'offset': offset, 'page_size': 17}
        table = ipc.open_stream(pa.BufferReader(wire.post(root + '/response-tables', query, binary=True))).read_all()
        if table.column_names != ref['field_ids'] or table.num_rows != min(17, ref['row_count'] - offset):
            raise AssertionError('Arrow transfer changed complete row/field shape')
        columns = [c.cast(pa.int64()).to_pylist() if pa.types.is_timestamp(c.type) else c.to_pylist() for c in table.columns]
        rows.extend(list(map(list, zip(*columns))))
    return {'columns': ref['field_ids'], 'rows': rows}


def selection(wire: Wire, case) -> dict:
    if case.manifest_sha256:
        facts = wire.call('GET', '/api/v1/models/' + case.model_ref, origin=PLATFORM)
        model = facts['model']
        if (model['model_ref'] != case.model_ref or model['digest'] != case.digest
                or model['manifest_sha256'] != case.manifest_sha256
                or model['producer_execution_id'] != case.producer or model['producer_fit_name'] != case.fit_name
                or model['producer_definition_id'] != case.cloud.producer_definition
                or model['producer_config_sha256'] != case.config_sha256
                or facts['manifest']['owner_user_id'] != case.owner):
            raise AssertionError('Saved resource selection changed')
    else:
        facts = wire.call('GET', '/api/v1/studies/executions/' + case.producer + '/model-fits/' + quote(case.fit_name, safe=''))
        if (facts['artifact']['summary']['digest'] != case.digest
                or facts['producer']['execution_id'] != case.producer
                or facts['producer']['definition_id'] != case.cloud.producer_definition
                or facts['producer_config_sha256'] != case.config_sha256):
            raise AssertionError('Temporary producer selection changed')
    return facts


def run_cloud(case, evidence: Path, token_provider, dispatch: str = 'inprocess') -> None:
    from .family_consumers import sources, compare_hmm_prefix, compare_hmm_replay, read_hmm_reference, compare_forecasts
    if dispatch != 'inprocess' or not evidence.is_absolute() or evidence.exists():
        raise ValueError('Use a new absolute cloud attempt and the normal server runtime')
    runtime_dependencies()  # Refuse an unprepared Arrow environment before credentials/writes.
    hmm_base = (read_hmm_reference(case.hmm_reference)
                if case.family == 'HMM' and case.stage != 'temporary' else None)
    evidence.mkdir(mode=0o700)
    # Admission-global fence survives failure, another output directory and lost replies.
    durable(case.cloud.path.with_suffix(case.cloud.path.suffix + '.spent'), {
        'admission_sha256': case.admission_sha256, 'executions': case.cloud.executions})
    durable(evidence / 'before-write-intent.json', {'admission_sha256': case.admission_sha256,
        'case_id': case.cloud.case_id, 'executions': case.cloud.executions,
        'source_sha256': case.cloud.source_sha256, 'request_plan': stage_plan(case.family, case.stage)})
    try:
        token = token_provider()
        if not isinstance(token, str) or not token:
            raise ValueError('The maintained provider returned no opaque credential')
        import httpx
        guard = RouteGuard(case)
        with httpx.Client(transport=httpx.HTTPTransport(retries=0, trust_env=False), timeout=30,
                          trust_env=False, follow_redirects=False,
                          headers={'Authorization': 'Bearer ' + token}) as client:
            wire = Wire(client, guard, evidence)
            if wire.call('GET', '/health').get('git_sha') != case.native_source:
                raise AssertionError('Actual served Native source changed')
            before = wire.call('GET', '/api/v1/studies/executions')
            if len(before) != len(set(before)) or len(before) > 50000 or set(before).intersection(case.cloud.executions):
                raise AssertionError('Reserved execution already exists or inventory is incomplete')
            first = selection(wire, case)
            answers, definitions = [], []
            for index, (source, execution) in enumerate(zip(sources(case), case.cloud.executions)):
                stage = evidence / f'consumer-{index}'
                definitions.append(launch(wire, case, source, execution, stage))
                answer = read_answer(wire, case, execution)
                durable(stage / 'answers.json', answer)
                answers.append(answer)
            if selection(wire, case) != first:
                raise AssertionError('Selected facts changed during consumption')
            after = wire.call('GET', '/api/v1/studies/executions')
            if len(after) != len(set(after)) or set(after) != set(before) | set(case.cloud.executions):
                raise AssertionError('Unexplained execution inventory changes')
            if case.family == 'HMM':
                if case.stage == 'temporary':
                    compare_hmm_prefix(*answers)
                    numeric = {'causal_prefix_rows': 5, 'observed_rows_per_consumer': 40}
                else:
                    compare_hmm_replay(hmm_base, answers[0])
                    numeric = {'observed_rows_per_consumer': 40, 'same_as_temporary_base': True}
            else:
                numeric = {'independent_horizon_oracles': compare_forecasts(case, answers[0])}
            plan = stage_plan(case.family, case.stage)
            write_counts = [guard.counts.get(('POST', NATIVE, path), 0) for path in (
                '/api/v1/script-validator/compile-view', '/api/v1/studies/definitions', '/api/v1/studies/executions')]
            table_count = sum(count for (method, _, path), count in guard.counts.items()
                              if method == 'POST' and path.endswith('/response-tables'))
            if (write_counts != [plan['consumer_executions']] * 3
                    or table_count != plan['Arrow_read_POSTs'] or guard.tables):
                raise AssertionError('Actual write/table-read counts differ from the admitted stage')
            durable(evidence / 'pending-receipt.json', {'status': 'PENDING_ACTUAL_SEALED_CONFIG_CAPTURE',
                'admission_sha256': case.admission_sha256, 'root_packet_sha256': case.root_packet_sha256,
                'case_id': case.cloud.case_id, 'owner_user_id': case.owner, 'family': case.family,
                'stage': case.stage, 'producer': case.producer, 'digest': case.digest,
                'model_ref': case.model_ref, 'manifest_sha256': case.manifest_sha256,
                'executions': list(case.cloud.executions), 'definitions': definitions,
                'fit_operations_per_consumer': [0] * len(answers), 'numeric': numeric,
                'requests': [{'method': k[0], 'origin': k[1], 'path': k[2], 'count': v} for k, v in guard.counts.items()],
                'scope': 'owned DEV API rows/effects only; sealed contracts pending; no campaign verdict'})
    except BaseException as error:
        durable(evidence / 'STOP.json', {'status': 'STOP_NO_REPLAY', 'error_type': type(error).__name__,
            'executions': case.cloud.executions, 'writes_must_stop': True})
        raise


def runtime_dependencies() -> None:
    # Ordinary installed dependencies only; no installer, SDK overlay or fallback.
    import httpx  # noqa: F401
    import pyarrow.ipc  # noqa: F401


def finalize(case, evidence: Path, capture: Path, digest: str) -> None:
    """Offline only: never fetch/export a config or submit another execution."""
    from .family_consumers import _read, _reference
    from .family_forecasts import retained_contract
    if not case.cloud or not evidence.is_absolute() or (evidence / 'STOP.json').exists():
        raise ValueError('Only a completed pending cloud attempt may be finalized')
    capture_body = _read(capture)
    root = json.loads(capture_body)
    if (hashlib.sha256(capture_body).hexdigest() != digest
            or root.get('kind') != 'ROOT_ADMITTED_DEV_MODEL_LIFECYCLE_CONFIG_CAPTURE_V1'
            or root.get('ready') is not True or root.get('admission_sha256') != case.admission_sha256
            or root.get('root_packet_sha256') != case.root_packet_sha256
            or root.get('case_id') != case.cloud.case_id):
        raise ValueError('Exact root-issued completed-config capture required')
    if Path(root['pending_receipt']['path']) != evidence / 'pending-receipt.json':
        raise ValueError('Capture refers to another pending attempt')
    pending_body, _ = _reference(root['pending_receipt'])
    pending = json.loads(pending_body)
    if (pending.get('status') != 'PENDING_ACTUAL_SEALED_CONFIG_CAPTURE'
            or pending.get('admission_sha256') != case.admission_sha256
            or pending.get('executions') != list(case.cloud.executions)
            or any(pending.get(k) != v for k, v in {
                'case_id': case.cloud.case_id, 'owner_user_id': case.owner, 'family': case.family,
                'stage': case.stage, 'producer': case.producer, 'digest': case.digest,
                'model_ref': case.model_ref, 'manifest_sha256': case.manifest_sha256}.items())
            or len(root['consumers']) != len(case.cloud.executions)):
        raise ValueError('Actual completed consumer inventory differs')
    witnesses = []
    for index, item in enumerate(root['consumers']):
        _, sha = _reference(item['config'])
        receipt_body, _ = _reference(item['capture_read_receipt'])
        receipt = json.loads(receipt_body)
        identity = {'owner_user_id': case.owner, 'execution_id': case.cloud.executions[index],
                    'definition_id': pending['definitions'][index], 'status': 'Completed', 'config_sha256': sha,
                    'source_sha256': case.cloud.source_sha256[index]}
        if (any(receipt.get(k) != v or item.get(k) != v for k, v in identity.items())
                or receipt.get('root_admitted') is not True or not item.get('completed_generation')
                or item['completed_generation'] != receipt.get('completed_generation')):
            raise ValueError('Config bytes lack an actual scoped completed generation capture')
        witness = retained_contract(Path(item['config']['path']), family=case.family,
            digest=case.digest, owner=case.owner, producer=case.producer, fit_cutoff=case.fit_cutoff,
            manifest_sha256=case.manifest_sha256, model_ref=case.model_ref)
        if witness.config_sha256 != sha:
            raise ValueError('Config changed between capture verification and contract inspection')
        from dataclasses import asdict
        witnesses.append(asdict(witness))
    durable(evidence / 'receipt.json', {**pending, 'status': 'passed', 'contract_witnesses': witnesses,
        'actual_config_capture_sha256': digest,
        'scope': 'root-admitted owned DEV API consumer and actual sealed-config capture; no campaign verdict'})
