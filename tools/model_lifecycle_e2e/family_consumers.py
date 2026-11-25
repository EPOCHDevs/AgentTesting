"""Explicit, root-admitted local consumers; no producer/Save/cleanup automation.

Run each temporary/saved/post-cleanup forecast case separately. HMM consumes the
same fit twice with an unchanged five-row prefix and a changed suffix. Unknown
write outcomes stop; existing evidence directories cannot be resubmitted.
Imports of SDK/network dependencies and the opaque token provider are lazy.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import struct
from typing import Callable, Literal, cast
from urllib.parse import quote, urlparse

from .families import Family, family_source, synthetic_value
from .family_forecasts import HORIZONS, forecast_source, retained_contract
from .forecast_oracle import arima_future, decode_arima, decode_garch, garch_future
from .native_artifact import ENVELOPE, MAX_BYTES

TOKEN_PROVIDER_PATH = Path('/home/adesola/EpochDev/EpochAI/tools/cognito_auth.py')
TOKEN_PROVIDER_SHA256 = '04281614f60473dac30325b23389c165d5ef9fe9282785250fb87170a242a9ed'

@dataclass(frozen=True)
class Case:
    family: Family
    stage: Literal['temporary', 'saved', 'post_cleanup']
    native_url: str
    platform_url: str
    native_root: Path
    native_source: str
    owner: str
    producer: str
    fit_name: str
    digest: str
    config_sha256: str
    source_sha256: str
    fit_cutoff: str
    artifact: Path
    model_ref: str
    manifest_sha256: str | None
    provider_file: Path
    provider_sha256: str
    admission_sha256: str
    root_packet_sha256: str
    producer_witness_sha256: str


def _read(path: Path, limit: int = MAX_BYTES) -> bytes:
    if not path.is_absolute() or path.is_symlink() or path.stat().st_size > limit:
        raise ValueError('Use an ordinary absolute bounded evidence file')
    return path.read_bytes()


def _reference(value: dict) -> tuple[bytes, str]:
    body = _read(Path(value['path']))
    digest = hashlib.sha256(body).hexdigest()
    if not re.fullmatch('[a-f0-9]{64}', value['sha256']) or digest != value['sha256']:
        raise ValueError('Referenced root/producer/cleanup evidence changed')
    return body, digest


def _loopback(value: str) -> str:
    parsed = urlparse(value)
    if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost'}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {'', '/'} or parsed.port is None):
        raise ValueError('This driver accepts explicitly admitted local origins only')
    return value.rstrip('/')


def load_case(path: Path, expected_sha256: str) -> Case:
    body = _read(path)
    if hashlib.sha256(body).hexdigest() != expected_sha256:
        raise ValueError('Case admission bytes changed')
    value = json.loads(body)
    if value.get('kind') != 'ROOT_ADMITTED_MODEL_LIFECYCLE_CONSUMER_V1' or value.get('ready') is not True:
        raise ValueError('An actual root-issued consumer admission is required')
    runtime_body, runtime_sha = _reference(value['root_runtime_packet'])
    runtime = json.loads(runtime_body)
    required_runtime = {
        'kind': 'ROOT_ADMITTED_PRIMARY_MODEL_LIFECYCLE_RUNTIME_V1', 'ready': True,
        'native_url': value['native_url'], 'platform_url': value['platform_url'],
        'native_root': value['native_root'], 'native_source': value['native_source'],
        'owner_user_id': value['owner_user_id'],
    }
    if runtime.get('ready') is not True or any(runtime.get(k) != v for k, v in required_runtime.items()):
        raise ValueError('Case configuration is not joined to actual primary readiness')
    producer_body, producer_sha = _reference(value['producer_descriptor'])
    producer = json.loads(producer_body)
    if (producer['owner_user_id'] != value['owner_user_id']
            or producer['execution_id'] != value['producer_execution_id']
            or producer['producer_config_sha256'] != value['producer_config_sha256']
            or hashlib.sha256(producer['producer']['study']['source'].encode()).hexdigest()
            != value['producer_source_sha256']):
        raise ValueError('The actual owned producer/config/source differs from admission')
    selected = [x for x in producer['fits'] if x['fit_name'] == value['fit_name']]
    if len(selected) != 1:
        raise ValueError('The selected producer fit is missing or ambiguous')
    selected = selected[0]['artifact']
    if (selected['binding']['digest'] != value['digest']
            or selected['manifest']['family'] != value['family']
            or selected['manifest']['owner_user_id'] != value['owner_user_id']
            or selected['manifest']['producer_execution_id'] != value['producer_execution_id']
            or selected['manifest']['fit_cutoff'] != value['fit_cutoff']):
        raise ValueError('The producer fit digest/family/cutoff differs from admission')
    location = producer['storage_location']
    if location not in {'server', 'shared_personal_v1'} or location != value['storage_location']:
        raise ValueError('Source storage mode cannot be relabelled')
    if location != 'server':
        generation_body, _ = _reference(value['completed_generation_witness'])
        generation = json.loads(generation_body)
        if (not isinstance(value['completed_generation'], (str, int))
                or isinstance(value['completed_generation'], bool)
                or value['completed_generation'] in ('', 0)
                or generation['execution_id'] != value['producer_execution_id']
                or generation['owner_user_id'] != value['owner_user_id']
                or generation['generation'] != value['completed_generation']):
            raise ValueError('Actual completed shared generation is not pinned')
    family, stage = value['family'], value['stage']
    if family not in {'HMM', 'ARIMA', 'GARCH'} or stage not in {'temporary', 'saved', 'post_cleanup'}:
        raise ValueError('Use one supported family and one explicit consumer stage')
    saved = stage != 'temporary'
    manifest = value.get('manifest_sha256')
    if saved and (not isinstance(manifest, str) or not re.fullmatch('[a-f0-9]{64}', manifest)):
        raise ValueError('Saved consumers require the exact saved manifest')
    if not saved and manifest is not None:
        raise ValueError('Temporary consumers cannot carry a saved-resource origin')
    if stage == 'post_cleanup':
        cleanup_body, _ = _reference(value['supported_cleanup_witness'])
        cleanup = json.loads(cleanup_body)
        if (cleanup.get('root_admitted') is not True
                or cleanup.get('owner_user_id') != value['owner_user_id']
                or cleanup.get('producer_execution_id') != value['producer_execution_id']
                or cleanup.get('digest') != value['digest']):
            raise ValueError('Post-cleanup requires actual scoped supported cleanup evidence')
    artifact = Path(value['artifact_path'])
    data = _read(artifact)
    if 'sha256:' + hashlib.sha256(data).hexdigest() != value['digest'] or not data.startswith(ENVELOPE):
        raise ValueError('Oracle artifact does not match the selected fit')
    offset = len(ENVELOPE)
    if len(data) < offset + 8:
        raise ValueError('Truncated oracle artifact envelope')
    size = struct.unpack_from('>Q', data, offset)[0]
    offset += 8
    if not size or offset + size >= len(data) or json.loads(data[offset:offset+size]) != selected['manifest']:
        raise ValueError('Oracle artifact manifest differs from the actual producer descriptor')
    if not value['owner_user_id'] or not re.fullmatch('[a-f0-9]{40}', value['native_source']):
        raise ValueError('Explicit expected owner and actual native source are required')
    native_root = Path(value['native_root'])
    if not native_root.is_absolute() or not native_root.is_dir():
        raise ValueError('Use the admitted absolute primary runtime storage root')
    provider = Path(value['token_provider_file'])
    if (provider != TOKEN_PROVIDER_PATH
            or value['token_provider_sha256'] != TOKEN_PROVIDER_SHA256
            or hashlib.sha256(_read(provider)).hexdigest() != value['token_provider_sha256']):
        raise ValueError('Use the admitted unchanged maintained opaque token provider')
    return Case(cast(Family, family), cast(Literal['temporary', 'saved', 'post_cleanup'], stage),
                _loopback(value['native_url']), _loopback(value['platform_url']),
                native_root, value['native_source'], value['owner_user_id'],
                value['producer_execution_id'], value['fit_name'], value['digest'],
                value['producer_config_sha256'], value['producer_source_sha256'],
                value['fit_cutoff'], artifact,
                value['saved_model_ref'] if saved else value['fit_name'], manifest,
                provider, value['token_provider_sha256'], expected_sha256, runtime_sha, producer_sha)


def sources(case: Case) -> tuple[str, ...]:
    origin = {'manifest_sha256': case.manifest_sha256} if case.manifest_sha256 else {
        'producer_execution': case.producer}
    if case.family == 'HMM':
        return (family_source('HMM', model_ref=case.model_ref, **origin),
                family_source('HMM', model_ref=case.model_ref,
                              changed_observed_suffix=True, **origin))
    return (forecast_source(case.family, model_ref=case.model_ref, **origin),)


def compare_forecasts(case: Case, result: dict) -> list[dict]:
    fields = (['horizon', 'forecast', 'forecast_lower', 'forecast_upper']
              if case.family == 'ARIMA' else ['horizon', 'forecast_variance', 'forecast_volatility'])
    if result['columns'] != fields or [x[0] for x in result['rows']] != list(HORIZONS):
        raise AssertionError('Missing, reordered or duplicated checkpoint scalar outputs')
    data = _read(case.artifact)
    decode = decode_arima if case.family == 'ARIMA' else decode_garch
    state = decode(data, digest=case.digest, owner=case.owner, execution=case.producer)
    if state.identity.fit_cutoff != case.fit_cutoff:
        raise AssertionError('The numerical oracle changed its forecast origin')
    if case.family == 'ARIMA' and (state.p != 0 or state.d != 1 or state.q != 0
                                  or not state.with_constant or state.training_count != 160):
        raise AssertionError('The ARIMA fixture was replaced with another recipe')
    checked = []
    for row in result['rows']:
        forecast = arima_future(state, row[0]) if case.family == 'ARIMA' else garch_future(state, row[0])
        expected = ([forecast.point, forecast.lower, forecast.upper] if case.family == 'ARIMA'
                    else [forecast.variance, forecast.volatility])
        if case.family == 'ARIMA':
            drift = (synthetic_value(159) - synthetic_value(0)) / 159
            if not math.isclose(forecast.point, synthetic_value(159) + row[0] * drift, abs_tol=1e-7):
                raise AssertionError('Stored ARIMA state disagrees with the independent training-row oracle')
        if len(row) != len(fields):
            raise AssertionError('A forecast row changed its complete declared shape')
        for actual, reference in zip(row[1:], expected):
            tolerance = 1e-7 if case.family == 'ARIMA' else 1e-12
            if (actual is None or not math.isfinite(actual)
                    or not math.isclose(actual, reference, rel_tol=1e-9, abs_tol=tolerance)):
                raise AssertionError('An actual checkpoint forecast differs from the independent oracle')
        checked.append(asdict(forecast))
    return checked


def compare_hmm_prefix(base: dict, changed: dict) -> None:
    if base['columns'] != ['coordinate', 'observed', 'p0', 'p1'] or changed['columns'] != base['columns']:
        raise AssertionError('HMM causal control changed its declared columns')
    if len(base['rows']) != 40 or len(changed['rows']) != 40:
        raise AssertionError('HMM causal control omitted observed rows')
    for table in (base, changed):
        for index, row in enumerate(table['rows']):
            expected_coordinate = int((datetime(2022, 1, 1, tzinfo=timezone.utc)
                + timedelta(days=160 + index)).timestamp()) * 1_000_000_000
            if type(row[0]) is not int or row[0] != expected_coordinate:
                raise AssertionError('HMM observed coordinate differs from the exact UTC-ns fixture grid')
    if base['rows'][:5] != changed['rows'][:5]:
        raise AssertionError('Changed suffix revised an earlier filtered probability')
    if [x[1] for x in base['rows'][5:]] == [x[1] for x in changed['rows'][5:]]:
        raise AssertionError('The suffix intervention did not actually change observations')
    for changed_suffix, table in enumerate((base, changed)):
        for index, (_, observed, p0, p1) in enumerate(table['rows']):
            expected = synthetic_value(160 + index) * (-1 if changed_suffix and index >= 5 else 1)
            if (not all(math.isfinite(x) for x in (observed, p0, p1))
                    or not math.isclose(observed, expected, abs_tol=1e-14)
                    or not 0 <= p0 <= 1 or not 0 <= p1 <= 1
                    or not math.isclose(p0 + p1, 1, rel_tol=0, abs_tol=1e-12)):
                raise AssertionError('Invalid HMM filtered probability output')


def run(case: Case, evidence: Path, token_provider: Callable[[], str | None],
        dispatch: str = 'inprocess') -> None:
    if not evidence.is_absolute():
        raise ValueError('Use an explicit absolute new evidence directory')
    if evidence.exists():
        raise FileExistsError('An existing attempt must not be resubmitted')
    token = token_provider()  # Opaque credential; no JWT/profile/cache inspection.
    if not isinstance(token, str) or not token:
        raise ValueError('The maintained token provider returned no opaque credential')
    # Lazy imports keep ordinary source/unit checks free of provider/SDK work.
    import httpx
    import pyarrow as pa
    import pyarrow.ipc as ipc
    from epoch_sdk.models_api import EpochModels
    from stratifyx import StratifyX
    from .http_owned import execute, write
    from .platform_owned import effects

    evidence.mkdir(mode=0o700)  # An existing attempt must never be resubmitted.
    prepared = sources(case)
    serialized_case = asdict(case)
    for key in ('native_root', 'artifact', 'provider_file'):
        serialized_case[key] = str(serialized_case[key])
    write(evidence / 'before-write-intent.json', {
        'case': serialized_case, 'source_sha256': [hashlib.sha256(x.encode()).hexdigest() for x in prepared],
        'expected_new_fits': 0, 'expected_consumer_executions': len(prepared),
        'scope': 'one explicit root-admitted consumer stage; no fit/Save/cleanup/proxy automation'})
    with httpx.Client(timeout=150, trust_env=False) as raw:
        native = StratifyX(base_url=case.native_url, token=token, httpx_client=raw, follow_redirects=False)
        models = EpochModels(base_url=case.platform_url, token=token, httpx_client=raw, follow_redirects=False)
        health = raw.get(case.native_url + '/health')
        health.raise_for_status()
        if health.json().get('git_sha') != case.native_source:
            raise AssertionError('Actual served Native source differs from primary admission')
        if case.manifest_sha256:
            detail = models.read_model(case.model_ref, request_options={'max_retries': 0})
            if (detail.model.digest != case.digest or detail.model.manifest_sha256 != case.manifest_sha256
                    or detail.model.producer_execution_id != case.producer
                    or detail.model.producer_fit_name != case.fit_name
                    or detail.model.producer_config_sha256 != case.config_sha256
                    or detail.manifest.get('owner_user_id') != case.owner):
                raise AssertionError('Actual saved facts changed the admitted owned selection')
        else:
            response = raw.get(case.native_url + '/api/v1/studies/executions/'
                               + quote(case.producer, safe='') + '/model-fits/'
                               + quote(case.fit_name, safe=''), headers={'Authorization': 'Bearer ' + token})
            response.raise_for_status()
            selection = response.json()
            if (selection['artifact']['summary']['digest'] != case.digest
                    or selection['producer']['execution_id'] != case.producer
                    or selection['producer_config_sha256'] != case.config_sha256):
                raise AssertionError('Actual temporary fit differs from the admitted producer')
        answers, executions, witnesses = [], [], []
        for index, source in enumerate(prepared):
            stage = evidence / f'consumer-{index}'
            _, execution = execute(native, source, 'Owned ' + case.family + ' ' + case.stage,
                                   stage, dispatch, checkpoint_only=case.family != 'HMM')
            executions.append(execution)
            effects(case.native_root, case.owner, execution, 0, stage / 'fit-effects.json')
            config = case.native_root / 'research_studies' / case.owner / execution / 'config.json'
            witness = retained_contract(config, family=case.family, digest=case.digest,
                owner=case.owner, producer=case.producer, fit_cutoff=case.fit_cutoff,
                manifest_sha256=case.manifest_sha256, model_ref=case.model_ref)
            witnesses.append(asdict(witness))
            output = native.sdk_requests.get_execution_outputs(execution, request_options={'max_retries': 0})
            response = native.research_responses.get_research_response(output.response_id, request_options={'max_retries': 0})
            parts = response.content.outputs
            name = 'observed' if case.family == 'HMM' else 'forecasts'
            if len(parts) != 1 or parts[0].name != name:
                raise AssertionError('The actual answer changed its declared output roster')
            ref = parts[0].source
            if ref.execution_id != execution:
                raise AssertionError('Answer rows came from another execution')
            rows = []
            for offset in range(0, ref.row_count, 17):
                body = b''.join(native.research_responses.read_research_response_table(execution,
                    table=ref.table, field_ids=ref.field_ids, offset=offset, page_size=17,
                    request_options={'max_retries': 0}))
                table = ipc.open_stream(pa.BufferReader(body)).read_all()
                if table.column_names != ref.field_ids or table.num_rows != min(17, ref.row_count-offset):
                    raise AssertionError('Arrow transfer omitted rows or changed declared field order')
                columns = [c.cast(pa.int64()).to_pylist() if pa.types.is_timestamp(c.type) else c.to_pylist()
                           for c in table.columns]
                rows.extend(list(map(list, zip(*columns))))
            answer = {'columns': ref.field_ids, 'rows': rows}
            write(stage / 'answers.json', answer)
            answers.append(answer)
        if case.family == 'HMM':
            compare_hmm_prefix(*answers)
            numeric = {'causal_prefix_rows': 5, 'observed_rows_per_consumer': 40}
        else:
            numeric = {'independent_horizon_oracles': compare_forecasts(case, answers[0])}
        write(evidence / 'receipt.json', {'status': 'passed', 'family': case.family, 'stage': case.stage,
            'digest': case.digest, 'producer': case.producer, 'model_ref': case.model_ref,
            'manifest_sha256': case.manifest_sha256, 'executions': executions,
            'fit_operations_per_consumer': [0] * len(executions), 'contract_witnesses': witnesses,
            'numeric': numeric, 'scope': 'actual local API consumer stage only; no AI/browser/cloud/campaign verdict'})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admission', type=Path, required=True)
    parser.add_argument('--admission-sha256', required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--dispatch', choices=['inprocess', 'subprocess'], default='inprocess')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    case = load_case(args.admission, args.admission_sha256)
    if not args.execute:
        parser.error('Source preparation only; actual root readiness and explicit --execute are required')
    spec = importlib.util.spec_from_file_location('epoch_primary_opaque_token_provider', case.provider_file)
    if spec is None or spec.loader is None:
        raise ValueError('Cannot load the maintained opaque token provider')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    provider = module.get_id_token
    run(case, args.evidence, provider, args.dispatch)


if __name__ == '__main__':
    main()
