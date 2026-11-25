"""Explicit provider-free family replay through real Native and Platform APIs.

Checks saved-copy identity, observed-output replay and retained fit effects.
Family math is checked through invariants here; the independent full OLS oracle
and native numerical-family oracle gates remain separate evidence.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
from pathlib import Path
from uuid import uuid4

import httpx
import pyarrow as pa
import pyarrow.ipc as ipc
from epoch_sdk.models_api import EpochModels
from stratifyx import StratifyX

from .families import family_source, synthetic_value
from .http_owned import execute, write
from .platform_owned import caller, effects, loopback


def tables(client: StratifyX, execution: str, evidence: Path) -> dict:
    outputs = client.sdk_requests.get_execution_outputs(execution, request_options={'max_retries': 0})
    assert outputs.response_id, 'The completed family run must publish an answer'
    response = client.research_responses.get_research_response(outputs.response_id, request_options={'max_retries': 0})
    found = {}
    for part in response.content.outputs:
        source = part.source
        assert source.execution_id == execution and part.name not in found
        rows = []
        for offset in range(0, source.row_count, 17):
            raw = b''.join(client.research_responses.read_research_response_table(execution,
                table=source.table, field_ids=source.field_ids, offset=offset, page_size=17,
                request_options={'max_retries': 0}))
            table = ipc.open_stream(pa.BufferReader(raw)).read_all()
            assert table.column_names == source.field_ids
            values = [column.cast(pa.int64()).to_pylist() if pa.types.is_timestamp(column.type)
                else column.to_pylist() for column in table.columns]
            page = list(map(list, zip(*values)))
            assert len(page) == min(17, source.row_count - offset)
            rows.extend(page)
        found[part.name] = {'columns': source.field_ids, 'rows': rows}
    assert set(found) == {'observed'}
    write(evidence / 'answers.json', {'tables': found, 'response_id': outputs.response_id,
        'response_version': outputs.response_version})
    return found


def invariants(family: str, answer: dict) -> None:
    result = answer['observed']
    assert len(result['rows']) == 40
    names = result['columns']
    assert names[:2] == ['coordinate', 'observed']
    coordinates = [row[0] for row in result['rows']]
    assert coordinates == sorted(set(coordinates))
    drift = (synthetic_value(159) - synthetic_value(0)) / 159
    for index, row in enumerate(result['rows']):
        assert all(value is not None and math.isfinite(value) for value in row)
        values = dict(zip(names, row))
        ordinal = 160 + index
        expected_time = int((datetime(2022, 1, 1, tzinfo=timezone.utc) + timedelta(days=ordinal)).timestamp()) * 1_000_000_000
        assert values['coordinate'] == expected_time
        assert math.isclose(values['observed'], synthetic_value(ordinal), abs_tol=1e-14)
        if family == 'HMM':
            assert 0 <= values['p0'] <= 1 and 0 <= values['p1'] <= 1
            assert math.isclose(values['p0'] + values['p1'], 1, abs_tol=1e-12)
        elif family == 'ARIMA':
            # ARIMA(0,1,0) with drift has an independent closed-form one-step oracle.
            assert math.isclose(values['fitted'], synthetic_value(ordinal - 1) + drift, abs_tol=1e-7)
            assert math.isclose(values['fitted'] + values['residual'], values['observed'], abs_tol=1e-12)
        elif family == 'GARCH':
            assert values['variance'] > 0 and values['volatility'] > 0
            assert math.isclose(values['volatility'] ** 2, values['variance'], rel_tol=1e-12)
            assert 0 <= values['persistence'] < 1
        else:
            raise ValueError('This fixture does not qualify another family')


def journey(args) -> None:
    args.evidence.mkdir(mode=0o700)
    token, owner = caller(args.expected_owner)
    native_url, platform_url = loopback(args.native_url), loopback(args.platform_url)
    with httpx.Client(timeout=150, trust_env=False) as native_http, httpx.Client(timeout=90, trust_env=False) as platform_http:
        native = StratifyX(base_url=native_url, token=token, httpx_client=native_http, follow_redirects=False)
        models = EpochModels(base_url=platform_url, token=token, httpx_client=platform_http, follow_redirects=False)
        fit_name = 'owned_' + args.family.lower()
        source = family_source(args.family, fit=True, model_ref=fit_name)
        _, producer = execute(native, source, 'Owned ' + args.family + ' producer', args.evidence / 'fit', args.dispatch)
        first = tables(native, producer, args.evidence / 'fit')
        invariants(args.family, first)
        effects(args.native_root, owner, producer, 1, args.evidence / 'fit-effects.json')
        # Metadata-only server descriptor, not an unbounded inventory payload read.
        reply = native_http.get(f'{native_url}/api/v1/studies/executions/{producer}/model-fit-output',
            headers={'Authorization': 'Bearer ' + token})
        reply.raise_for_status()
        envelope = reply.json()
        raw = base64.b64decode(envelope['descriptor_base64'], validate=True)
        assert hashlib.sha256(raw).hexdigest() == envelope['descriptor_sha256']
        descriptor = json.loads(raw)
        assert descriptor['owner_user_id'] == owner and descriptor['execution_id'] == producer
        assert descriptor['storage_location'] == 'server' and len(descriptor['fits']) == 1
        selection = descriptor['fits'][0]
        assert selection['fit_name'] == fit_name and selection['artifact']['manifest']['family'] == args.family
        digest = selection['artifact']['binding']['digest']
        (args.evidence / 'producer-model-fits.json').write_bytes(raw)
        _, continuation = execute(native, family_source(args.family, model_ref=fit_name, producer_execution=producer),
            'Owned ' + args.family + ' continuation', args.evidence / 'continue', args.dispatch)
        continued = tables(native, continuation, args.evidence / 'continue')
        assert continued == first
        effects(args.native_root, owner, continuation, 0, args.evidence / 'continue-effects.json')
        request = {'request_id': str(uuid4()), 'execution_id': producer, 'fit_name': fit_name,
            'expected_digest': digest, 'name': 'Owned ' + args.family + ' saved replay', 'source_location': 'server'}
        write(args.evidence / 'save-intent.json', request)
        saved = models.save_model(**request, request_options={'max_retries': 0})
        assert saved.digest == digest
        assert models.save_model(**request, request_options={'max_retries': 0}) == saved
        detail = models.read_model(saved.model_ref, request_options={'max_retries': 0})
        assert detail.producer['study']['source'] == source
        _, consumer = execute(native, family_source(args.family, model_ref=saved.model_ref, manifest_sha256=saved.manifest_sha256),
            'Owned ' + args.family + ' saved copy', args.evidence / 'saved', args.dispatch)
        consumed = tables(native, consumer, args.evidence / 'saved')
        assert consumed == first
        invariants(args.family, consumed)
        effects(args.native_root, owner, consumer, 0, args.evidence / 'saved-effects.json')
        write(args.evidence / 'receipt.json', {'status': 'passed', 'family': args.family, 'owner': owner,
            'dispatch': args.dispatch, 'producer': producer, 'continuation': continuation, 'saved_consumer': consumer,
            'digest': digest, 'model_ref': saved.model_ref, 'manifest_sha256': saved.manifest_sha256,
            'producer_fit_count': 1, 'continuation_fit_count': 0, 'saved_consumer_fit_count': 0,
            'scope': 'actual private API/SDK observed replay, all-row input identity/invariants and closed-form ARIMA(0,1,0) prediction oracle; no independent HMM/GARCH estimation oracle, forecast, UI, AI or direct-S3 acceptance'})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--family', choices=['HMM', 'ARIMA', 'GARCH'], required=True)
    parser.add_argument('--native-url', required=True)
    parser.add_argument('--platform-url', required=True)
    parser.add_argument('--native-root', type=Path, required=True)
    parser.add_argument('--expected-owner', required=True)
    parser.add_argument('--root-readiness', type=Path, required=True)
    parser.add_argument('--root-readiness-sha256', required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--dispatch', choices=['inprocess', 'subprocess'], default='inprocess')
    args = parser.parse_args()
    if not all(path.is_absolute() for path in [args.native_root, args.evidence]):
        raise ValueError('Use explicit absolute owned fixture paths')
    from .opaque_auth import admit_primary
    admit_primary(args.root_readiness,args.root_readiness_sha256,native_url=args.native_url,
        platform_url=args.platform_url,native_root=args.native_root,expected_owner=args.expected_owner)
    journey(args)


if __name__ == '__main__': main()
