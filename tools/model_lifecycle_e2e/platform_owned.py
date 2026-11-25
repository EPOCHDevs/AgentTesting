"""Phased, provider-free API proof for Platform Save and exact native OLS reuse.

Explicit private services through loopback origins and a synthetic fixture only. No campaign grade,
AI/vendor call, shared stack refresh or unknown-outcome resubmission. Stop/restart
the separately launched private services between produce/offline/consume phases.
Retain every attempt directory, including failures.
"""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import socket
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from epoch_sdk.models_api import EpochModels
from stratifyx import StratifyX

from .http_effects import fit_count, stable_lines
from .http_owned import execute, table_answers, write
from .native_artifact import assert_fixture_state, read_owned_ols
from .oracle import expected
from .source import fixture_source
from .opaque_auth import TokenProvider, admit_primary, caller


def loopback(origin: str) -> str:
    parsed = urlsplit(origin)
    if parsed.scheme != 'http' or parsed.hostname not in {'localhost', '127.0.0.1'} or parsed.path not in {'', '/'} or parsed.query or parsed.fragment or parsed.username:
        raise ValueError('Choose explicit loopback fixture service origins')
    return origin.rstrip('/')


def effects(root: Path, owner: str, execution: str, count: int, destination: Path) -> None:
    job = root / 'research_studies' / owner / execution
    logs, log_sha = stable_lines(job / 'logs.jsonl')
    events, event_sha = stable_lines(job / 'events.jsonl')
    write(destination, fit_count(logs, events, execution, count) | {'logs_sha256': log_sha, 'events_sha256': event_sha})


@dataclass(frozen=True)
class ObservedSuccessfulSave:
    model_ref: str
    digest: str
    manifest_sha256: str


class LostSaveResponse(httpx.BaseTransport):
    """Drop one actual successful HTTP reply before the generated SDK sees it."""
    def __init__(self, evidence: Path):
        self.inner = httpx.HTTPTransport(trust_env=False)
        self.evidence = evidence
        self.calls = 0
        self.dropped = False
        self.observed_success: ObservedSuccessfulSave | None = None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        assert self.calls == 1 and request.method == 'POST' and request.url.path == '/api/v1/models'
        response = self.inner.handle_request(request)
        body = bytearray()
        try:
            for part in response.iter_raw():
                body.extend(part)
                if len(body) > 64 * 1024:
                    raise ValueError('The owned Save reply exceeds the response-loss fixture bound')
        finally:
            response.close()
        if response.status_code not in (200, 201):
            return httpx.Response(response.status_code, headers=response.headers, content=bytes(body), request=request)
        self.dropped = True
        parsed = json.loads(body)
        values = [parsed.get(k) for k in ('model_ref', 'digest', 'manifest_sha256')]
        if any(not isinstance(value, str) or not value for value in values):
            raise ValueError('Actual Save reply is missing exact observed-success pins; stop writes')
        self.observed_success = ObservedSuccessfulSave(*values)
        write(self.evidence, {'fault': 'client transport drops actual successful HTTP response',
            'http_status': response.status_code, 'body_sha256': hashlib.sha256(body).hexdigest(),
            'body_bytes': len(body), 'calls': self.calls,
            'scope': 'actual service request succeeded; generated client receives no result; not mid-publication worker recovery'})
        raise httpx.RemoteProtocolError('Owned fixture dropped the accepted Save response', request=request)

    def close(self) -> None:
        self.inner.close()


def produce(args, token_provider: TokenProvider | None = None) -> None:
    args.evidence.mkdir(mode=0o700)
    token, owner = caller(args.expected_owner, token_provider)
    oracle = expected(args.fixture)
    native_url, platform_url = loopback(args.native_url), loopback(args.platform_url)
    with httpx.Client(timeout=120, trust_env=False) as native_http, httpx.Client(timeout=90, trust_env=False) as model_http:
        native = StratifyX(base_url=native_url, token=token, httpx_client=native_http, follow_redirects=False)
        models = EpochModels(base_url=platform_url, token=token, httpx_client=model_http, follow_redirects=False)
        initial = models.list_models(request_options={'max_retries': 0})
        assert initial.items == [], 'Choose a fresh private model store'
        source = fixture_source(args.fixture, stage='fit')
        definition, producer = execute(native, source, f'Owned Platform producer ({args.dispatch})', args.evidence / 'fit', args.dispatch)
        table_answers(native, producer, args.evidence / 'fit', oracle['holdout'])
        response = native_http.get(f'{native_url}/api/v1/studies/executions/{producer}/model-fit-output', headers={'Authorization': f'Bearer {token}'})
        response.raise_for_status()
        body = response.json()
        descriptor_bytes = base64.b64decode(body['descriptor_base64'], validate=True)
        assert hashlib.sha256(descriptor_bytes).hexdigest() == body['descriptor_sha256']
        descriptor = json.loads(descriptor_bytes)
        assert descriptor['owner_user_id'] == owner and descriptor['execution_id'] == producer
        assert descriptor['producer']['study']['source'] == source
        assert descriptor['storage_location'] == 'server' and len(descriptor['fits']) == 1
        fit = descriptor['fits'][0]
        digest = fit['artifact']['binding']['digest']
        (args.evidence / 'producer-model-fits.json').write_bytes(descriptor_bytes)
        assert models.list_models().items == [], 'Fitting alone must not save a library model'
        _, continuation = execute(native, fixture_source(args.fixture, stage='continue', producer_execution=producer),
            f'Owned exact continuation ({args.dispatch})', args.evidence / 'continue', args.dispatch)
        table_answers(native, continuation, args.evidence / 'continue', oracle['continuation'])
        effects(args.native_root, owner, producer, 1, args.evidence / 'fit-effects.json')
        effects(args.native_root, owner, continuation, 0, args.evidence / 'continue-effects.json')
        request = {'request_id': str(uuid4()), 'name': f'Owned OLS Platform {args.dispatch}', 'execution_id': producer,
            'fit_name': fit['fit_name'], 'expected_digest': digest, 'source_location': 'server'}
        write(args.evidence / 'save-intent.json', request)
        before = sorted(path.name for path in (args.native_root / 'research_studies' / owner).iterdir())
        if args.drop_first_save_response:
            loss = LostSaveResponse(args.evidence / 'lost-save-response.json')
            with httpx.Client(timeout=90, trust_env=False, transport=loss) as lost_http:
                lost_client = EpochModels(base_url=platform_url, token=token, httpx_client=lost_http, follow_redirects=False)
                try:
                    lost_client.save_model(**request, request_options={'max_retries': 0})
                except Exception:
                    if not loss.dropped or loss.calls != 1 or loss.observed_success is None:
                        raise
                else:
                    raise AssertionError('The generated client unexpectedly received the dropped Save response')
            observed = loss.observed_success
            if observed is None:
                raise ValueError('Unknown actual Save outcome; stop for read-only reconciliation')
            reconciled = models.read_model(observed.model_ref, request_options={'max_retries': 0})
            assert observed.digest == digest == reconciled.model.digest
            assert observed.manifest_sha256 == reconciled.model.manifest_sha256
        saved = models.save_model(**request, request_options={'max_retries': 0})
        with ThreadPoolExecutor(max_workers=4) as pool:
            repeats = list(pool.map(lambda _: models.save_model(**request, request_options={'max_retries': 0}), range(8)))
        assert all(item == saved for item in repeats)
        after = sorted(path.name for path in (args.native_root / 'research_studies' / owner).iterdir())
        assert before == after, 'Save/retry must create no numerical execution'
        facts = models.read_model(saved.model_ref)
        assert saved.digest == digest and facts.producer['study']['source'] == source
        personal = args.native_root / 'personal_content' / 'owners' / owner.encode().hex()
        original = personal / 'models/v1/artifacts' / digest[7:]
        copied = personal / 'saved-models/v1/artifacts' / digest[7:]
        assert original.read_bytes() == copied.read_bytes()
        state = read_owned_ols(copied, digest=digest, owner=owner, execution=producer)
        assert_fixture_state(state, args.fixture)
        write(args.evidence / 'retained-ols-state.json', asdict(state))
        write(args.evidence / 'saved-summary.json', saved.model_dump(mode='json'))
        write(args.evidence / 'saved-facts.json', facts.model_dump(mode='json'))
        platform_launch = json.loads((args.native_root.parent / 'platform-export-launch.json').read_text())
        assert platform_launch['port'] == urlsplit(platform_url).port
        write(args.evidence / 'receipt.json', {'status': 'saved', 'scope': 'provider-free private server transfer; no AI/Portal/S3 acceptance',
            'owner': owner, 'dispatch': args.dispatch, 'definition_id': definition,
            'producer_execution': producer, 'continuation_execution': continuation,
            'model_ref': saved.model_ref, 'digest': digest, 'manifest_sha256': saved.manifest_sha256,
            'native_url': native_url, 'native_root': str(args.native_root), 'platform_url': platform_url,
            'fixture_sha256': hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
            'platform_pid_before_restart': platform_launch['pid'], 'fit_count': 1, 'continuation_fit_count': 0,
            'lost_accepted_save_response': bool(args.drop_first_save_response),
            'concurrent_equal_saves': len(repeats), 'save_numerical_executions': 0})


def authored(args, token_provider: TokenProvider | None = None) -> None:
    """Only the authored fit is saved; unselected temporary fits remain temporary."""
    args.evidence.mkdir(mode=0o700)
    token, owner = caller(args.expected_owner, token_provider)
    native_url, platform_url = loopback(args.native_url), loopback(args.platform_url)
    source = fixture_source(args.fixture, stage='fit', save_as='Owned authored OLS')
    anchor = 'score_x = '
    at = source.index(anchor)
    source = source[:at] + 'unselected = ols_fit(model_ref="unselected_owned")(train_x, target=train_y, training_source_coordinate=index_of(train_y))\n' + source[at:]
    with httpx.Client(timeout=120, trust_env=False) as native_http, httpx.Client(timeout=90, trust_env=False) as model_http:
        native = StratifyX(base_url=native_url, token=token, httpx_client=native_http, follow_redirects=False)
        models = EpochModels(base_url=platform_url, token=token, httpx_client=model_http, follow_redirects=False)
        _, producer = execute(native, source, 'Owned explicit authored Save', args.evidence / 'fit', args.dispatch)
        table_answers(native, producer, args.evidence / 'fit', expected(args.fixture)['holdout'])
        effects(args.native_root, owner, producer, 2, args.evidence / 'fit-effects.json')
        request = {'execution_id': producer, 'source_location': 'server'}
        write(args.evidence / 'completion-intent.json', request)
        before = sorted(path.name for path in (args.native_root / 'research_studies' / owner).iterdir())
        page = models.complete_model_saves(**request, request_options={'max_retries': 0})
        repeated = models.complete_model_saves(**request, request_options={'max_retries': 0})
        assert page == repeated and len(page.items) == 1 and not page.next_cursor
        saved = page.items[0]
        assert saved.name == 'Owned authored OLS' and saved.producer_fit_name == 'owned_fixture'
        detail = models.read_model(saved.model_ref, request_options={'max_retries': 0})
        assert detail.producer['study']['source'] == source
        after = sorted(path.name for path in (args.native_root / 'research_studies' / owner).iterdir())
        assert before == after
        write(args.evidence / 'authored-receipt.json', {'status': 'passed', 'owner': owner, 'producer': producer,
            'fit_count': 2, 'saved_count': 1, 'completion_numerical_executions': 0,
            'model_ref': saved.model_ref, 'digest': saved.digest, 'manifest_sha256': saved.manifest_sha256,
            'unselected_fit': 'unselected_owned', 'scope': 'actual explicit completion API and repeated receipt; private local direct execution does not establish cloud acceptance-worker recovery'})


def offline(args, token_provider: TokenProvider | None = None) -> None:
    receipt = json.loads((args.evidence / 'receipt.json').read_text())
    parsed = urlsplit(loopback(args.native_url))
    with socket.socket() as probe:
        assert probe.connect_ex((parsed.hostname, parsed.port)) != 0, 'Stop only the explicitly owned private Backend before this phase'
    platform_launch = json.loads((args.native_root.parent / 'platform-export-launch.json').read_text())
    assert platform_launch['pid'] != receipt['platform_pid_before_restart'], 'Restart the owned private Platform and retain the actual changed launch PID'
    token, owner = caller(args.expected_owner, token_provider)
    assert owner == receipt['owner']
    request = json.loads((args.evidence / 'save-intent.json').read_text())
    with httpx.Client(timeout=30, trust_env=False) as http:
        models = EpochModels(base_url=loopback(args.platform_url), token=token, httpx_client=http, follow_redirects=False)
        facts = models.read_model(receipt['model_ref'])
        assert facts.model.digest == receipt['digest'] and facts.model.manifest_sha256 == receipt['manifest_sha256']
        assert models.save_model(**request).model_ref == receipt['model_ref']
        assert any(item.model_ref == receipt['model_ref'] for item in models.list_models().items)
    destination = args.evidence / 'offline-restart.json'
    if destination.exists(): raise ValueError('Retain prior evidence; choose a new attempt instead of overwriting')
    write(destination, {'status': 'passed', 'native_port_closed': True, 'reads_and_equal_retry_without_backend': True,
        'model_ref': receipt['model_ref'], 'digest': receipt['digest'], 'manifest_sha256': receipt['manifest_sha256'],
        'platform_pid_before': receipt['platform_pid_before_restart'], 'platform_pid_after': platform_launch['pid']})


def detach(args, token_provider: TokenProvider | None = None) -> None:
    assert (args.evidence / 'offline-restart.json').is_file(), 'Prove independent offline reads before detaching source'
    receipt = json.loads((args.evidence / 'receipt.json').read_text())
    parsed = urlsplit(loopback(args.native_url))
    with socket.socket() as probe:
        assert probe.connect_ex((parsed.hostname, parsed.port)) != 0, 'Detach only while the explicitly owned Native server is stopped'
    root = args.native_root.resolve()
    if root.parent != args.evidence.resolve().parent or root.name != 'native-export-server':
        raise ValueError('Source-detach proof is restricted to the exact sibling private native-export-server fixture')
    launch = json.loads((root.parent / 'native-export-launch.json').read_text())
    assert Path(launch['storage_root']).resolve() == root and launch['port'] == parsed.port
    _, owner = caller(args.expected_owner, token_provider)
    assert owner == receipt['owner']
    backup = args.evidence / 'original-sources-retained'
    backup.mkdir()
    personal = root / 'personal_content' / 'owners' / owner.encode().hex()
    (personal / 'models/v1').rename(backup / 'native-models-v1')
    (root / 'research_studies' / owner / receipt['producer_execution']).rename(backup / 'producer-execution')
    write(args.evidence / 'source-detach.json', {'status': 'detached', 'native_port_closed': True,
        'native_pid_before_restart': launch['pid'], 'backup': str(backup),
        'preserved': ['native models/v1', 'original producer execution'],
        'scope': 'exact private fixture resources moved with backups; no deletion'})


def consume(args, token_provider: TokenProvider | None = None) -> None:
    receipt = json.loads((args.evidence / 'receipt.json').read_text())
    assert (args.evidence / 'offline-restart.json').is_file(), 'Qualify offline/restart reads first'
    token, owner = caller(args.expected_owner, token_provider)
    assert owner == receipt['owner']
    detached = args.evidence / 'source-detach.json'
    if args.remove_native_sources:
        assert detached.is_file(), 'Run detach while the owned Native server is stopped, then restart before consumption'
        moved = json.loads(detached.read_text())
        launch = json.loads((args.native_root.parent / 'native-export-launch.json').read_text())
        assert launch['pid'] != moved['native_pid_before_restart'], 'Restart the private Native after source detachment'
        assert Path(launch['storage_root']).resolve() == args.native_root.resolve()
    with httpx.Client(timeout=120, trust_env=False) as native_http, httpx.Client(timeout=30, trust_env=False) as model_http:
        models = EpochModels(base_url=loopback(args.platform_url), token=token, httpx_client=model_http, follow_redirects=False)
        facts = models.read_model(receipt['model_ref'])
        assert facts.model.digest == receipt['digest'] and facts.model.manifest_sha256 == receipt['manifest_sha256']
        source = fixture_source(args.fixture, stage='saved', model_ref=receipt['model_ref'], model_manifest_sha256=receipt['manifest_sha256'])
        native = StratifyX(base_url=loopback(args.native_url), token=token, httpx_client=native_http, follow_redirects=False)
        _, execution = execute(native, source, f'Owned saved model reuse ({args.dispatch})', args.evidence / 'saved', args.dispatch)
        table_answers(native, execution, args.evidence / 'saved', expected(args.fixture)['continuation'])
        effects(args.native_root, owner, execution, 0, args.evidence / 'saved-effects.json')
        final = receipt | {'status': 'passed', 'saved_consumer_execution': execution, 'saved_consumer_fit_count': 0,
            'original_native_sources_removed_with_backups': bool(args.remove_native_sources),
            'scope': 'private real API/SDK/Arrow numerical server-export journey; no AI/Portal/direct-S3 acceptance'}
        write(args.evidence / 'final-receipt.json', final)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['produce', 'authored', 'offline', 'detach', 'consume'])
    parser.add_argument('--native-url', required=True)
    parser.add_argument('--platform-url', required=True)
    parser.add_argument('--native-root', type=Path, required=True)
    parser.add_argument('--expected-owner', required=True)
    parser.add_argument('--root-readiness', type=Path, required=True)
    parser.add_argument('--root-readiness-sha256', required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--dispatch', choices=['inprocess', 'subprocess'], default='inprocess')
    parser.add_argument('--remove-native-sources', action='store_true')
    parser.add_argument('--drop-first-save-response', action='store_true',
        help='Inject a client transport failure after an actual successful Save response, then reconcile the same intent')
    args = parser.parse_args()
    if not all(path.is_absolute() for path in (args.native_root, args.fixture, args.evidence)):
        raise ValueError('Use exact absolute private fixture/evidence paths')
    admit_primary(args.root_readiness, args.root_readiness_sha256, native_url=args.native_url,
        platform_url=args.platform_url, native_root=args.native_root, expected_owner=args.expected_owner)
    {'produce': produce, 'authored': authored, 'offline': offline, 'detach': detach, 'consume': consume}[args.phase](args)


if __name__ == '__main__': main()
