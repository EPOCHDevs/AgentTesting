"""Source and retained-contract checks for exact checkpoint consumers.

These helpers prepare/check evidence; they do not submit a study or invent a
future calendar. The original observed family generator remains separate.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Literal

HORIZONS = (1, 2, 5, 21)
ForecastFamily = Literal['ARIMA', 'GARCH']


def forecast_source(family: ForecastFamily, *, model_ref: str,
                    producer_execution: str | None = None,
                    manifest_sha256: str | None = None) -> str:
    if family not in ('ARIMA', 'GARCH'):
        raise ValueError('Choose a declared checkpoint forecast family')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,254}', model_ref):
        raise ValueError('Use the exact fixture fit or saved reference')
    if bool(producer_execution) == bool(manifest_sha256):
        raise ValueError('Select exactly one temporary execution or saved manifest origin')
    if manifest_sha256 and not re.fullmatch('[a-f0-9]{64}', manifest_sha256):
        raise ValueError('An exact saved manifest pin is required')
    origin = (f',fit_execution_id={json.dumps(producer_execution)}' if producer_execution else
              f',model_manifest_sha256={json.dumps(manifest_sha256)}')
    options = ('p=0,d=1,q=0,with_constant=True,confidence_level=0.95'
               if family == 'ARIMA' else 'p=1,q=1,distribution=normal')
    fields = (('forecast', 'forecast_lower', 'forecast_upper') if family == 'ARIMA'
              else ('forecast_variance', 'forecast_volatility'))
    source = '# Owned immutable checkpoint forecasts; no observation inputs or fitting.\n'
    selects = []
    relations = []
    for horizon in HORIZONS:
        node, relation = f'f{horizon}', f'r{horizon}'
        source += (f'{node} = {family.lower()}_forecast_from_fit(model_ref={json.dumps(model_ref)},'
                   f'{options},horizon={horizon}{origin})()\n')
        source += (f'{relation} = relation(columns={fields!r})('
                   + ','.join(f'{node}.{field}' for field in fields) + ')\n')
        relations.append(relation)
        selects.append(f'SELECT CAST({horizon} AS BIGINT) AS horizon,' + ','.join(fields)
                       + f' FROM {relation}')
    query = ' UNION ALL '.join(selects) + ' ORDER BY horizon'
    columns = ['relation_field("horizon",Int64)'] + [
        f'relation_field({json.dumps(field)},Float64)' for field in fields]
    source += ('answer = sql_program(queries={"forecasts":' + json.dumps(query)
               + '},outputs={"forecasts":relation_output(columns=[' + ','.join(columns)
               + '],row_key=("horizon",),order_by=("horizon",))})('
               + ','.join(relations) + ')\n'
               + 'response_table(name="forecasts",source=answer.forecasts)\n')
    return source


@dataclass(frozen=True)
class ContractWitness:
    config_sha256: str
    node_ids: tuple[str, ...]
    digest: str
    capability: str
    effective_use: str
    horizons: tuple[int, ...]


def retained_contract(config: Path, *, family: str, digest: str, owner: str,
                      producer: str, fit_cutoff: str, manifest_sha256: str | None,
                      model_ref: str) -> ContractWitness:
    body = config.read_bytes()
    if len(body) > 16 * 1024 * 1024:
        raise ValueError('Retained execution configuration exceeds the evidence bound')
    # Open serialized input is validated here; only the closed witness escapes.
    value = json.loads(body)
    nodes = value['execution_plan']['nodes']
    expected_type = family.lower() + ('_infer' if family == 'HMM' else '_forecast_from_fit')
    selected = []
    horizons = []
    for node in nodes:
        runtime = json.loads(node['runtime_contract_json'])
        if 'FitCapable' in runtime.get('modelCapabilities', []):
            raise AssertionError('A consumer plan unexpectedly contains a fit-capable node')
        if node['type'] != expected_type:
            continue
        seal = json.loads(node['model_artifact_contract_json'])
        binding = seal['resolved_binding']
        if (seal['role'] != 'Consumer' or seal['owner_user_id'] != owner
                or seal['family'] != family or seal['model_ref'] != model_ref
                or binding['digest'] != digest or binding['fit_cutoff'] != fit_cutoff):
            raise AssertionError('The compiled consumer changed its exact selected binding')
        if manifest_sha256:
            resource = seal['saved_model_resource']
            if resource['model_ref'] != model_ref or resource['manifest_sha256'] != manifest_sha256:
                raise AssertionError('The compiled consumer lost the selected saved manifest')
        elif seal.get('source_execution_id') != producer or seal['binding_kind'] != 'ExecutionFit':
            raise AssertionError('The compiled consumer changed its producer execution')
        capability = 'ObservedInference' if family == 'HMM' else 'ForecastCapable'
        if (runtime['modelOutputUse'] != 'ObservedCausal'
                or node['model_output_use'] != 'ObservedCausal'
                or capability not in runtime['modelCapabilities'] or seal['capability'] != capability):
            raise AssertionError('Actual direct/effective causal mode or capability differs')
        if family != 'HMM':
            forecast = seal['forecast']
            if (seal['as_of'] != fit_cutoff or forecast['origin_policy'] != 'FitCheckpoint'
                    or forecast['origin_input_id'] != '__model_artifact'
                    or forecast['horizon_unit'] != 'InputObservations'
                    or forecast['target_policy'] != 'OriginPlusHorizon'):
                raise AssertionError('A forecast changed its untouched checkpoint origin')
            horizons.append(forecast['horizon_count'])
        selected.append(node['id'])
    expected_count = 1 if family == 'HMM' else len(HORIZONS)
    if len(selected) != expected_count or (family != 'HMM' and sorted(horizons) != list(HORIZONS)):
        raise AssertionError('The retained plan omitted or duplicated a required consumer/horizon')
    return ContractWitness(hashlib.sha256(body).hexdigest(), tuple(selected), digest,
                           'ObservedInference' if family == 'HMM' else 'ForecastCapable',
                           'ObservedCausal', tuple(sorted(horizons)))
