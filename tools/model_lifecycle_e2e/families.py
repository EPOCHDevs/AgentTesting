"""Owned non-vendor source for the implemented immutable-artifact families.

This prepares source only. Numerical/runtime qualification is separate and must
not be inferred from a source-generation test. PCA/scalers/clustering are not
advertised as saved-artifact consumers by this fixture.
"""
from __future__ import annotations

from datetime import date, timedelta
import json
import math
import re
from typing import Literal

Family = Literal['HMM', 'ARIMA', 'GARCH']


def synthetic_value(index: int) -> float:
    return 0.02 * math.sin(index * 0.37) + 0.01 * math.cos(index * 0.11) + (0.015 if index % 40 >= 20 else -0.015)


def family_source(family: Family, *, model_ref: str, producer_execution: str | None = None,
    manifest_sha256: str | None = None, fit: bool = False, save_as: str | None = None) -> str:
    if family not in ('HMM', 'ARIMA', 'GARCH'):
        raise ValueError('Choose an actually implemented artifact family')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,254}', model_ref):
        raise ValueError('Use an exact local-fit or saved resource reference')
    if fit and (producer_execution or manifest_sha256) or not fit and bool(producer_execution) == bool(manifest_sha256):
        raise ValueError('Fit once, or choose exactly one execution/resource origin')
    if manifest_sha256 is not None and not re.fullmatch('[a-f0-9]{64}', manifest_sha256):
        raise ValueError('An exact saved manifest pin is required')
    if save_as is not None and (not fit or not save_as or len(save_as.encode()) > 255 or any(ord(c) < 32 for c in save_as)):
        raise ValueError('An authored Save name belongs only to a fit producer')
    first = date(2022, 1, 1)
    rows = []
    for i in range(200):
        # A fixed owned regime/volatility signal, not sampled vendor data.
        value = synthetic_value(i)
        rows.append(f"({i}, TIMESTAMPTZ '{first + timedelta(days=i)}T00:00:00Z', CAST('{value:.17g}' AS DOUBLE))")
    query = 'SELECT row_id, coordinate, value FROM (VALUES ' + ', '.join(rows) + ') AS owned(row_id,coordinate,value) ORDER BY coordinate,row_id'
    source = '''# Synthetic immutable-artifact family fixture; no provider observations.
raw = sql_program(queries={"rows": %s}, outputs={"rows": relation_output(columns=[
    relation_field("row_id", Int64), relation_field("coordinate", TimestampNsUtc),
    relation_field("value", Float64)], row_key=("row_id",), order_by=("coordinate", "row_id"))})()
values = series(raw.rows.coordinate, raw.rows.value)
train = clip_window(window=SourceWindow(start="2022-01-01", end="2022-06-09"))(values)
observed = clip_window(window=SourceWindow(start="2022-06-10", end="2022-07-19"))(values)
''' % json.dumps(query)
    options = {'HMM': 'num_states=2', 'ARIMA': 'p=0,d=1,q=0,with_constant=True', 'GARCH': 'p=1,q=1,distribution=normal'}[family]
    origin = f',fit_execution_id={json.dumps(producer_execution)}' if producer_execution else \
        f',model_manifest_sha256={json.dumps(manifest_sha256)}' if manifest_sha256 else ''
    inputs = '(train,)' if family == 'HMM' else 'train'
    if fit:
        save = f',save_as={json.dumps(save_as)}' if save_as is not None else ''
        source += f'fit = {family.lower()}_fit(model_ref={json.dumps(model_ref)},{options},random_state=42{save})({inputs})\n'
    observed_input = '(observed,)' if family == 'HMM' else 'observed'
    source += f'inference = {family.lower()}_infer(model_ref={json.dumps(model_ref)},{options}{origin})({observed_input})\n'
    fields = {
        'HMM': [('p0', 'inference.state_probs[0]'), ('p1', 'inference.state_probs[1]')],
        'ARIMA': [('fitted', 'inference.fitted'), ('residual', 'inference.residual')],
        'GARCH': [('variance', 'inference.conditional_variance'), ('volatility', 'inference.volatility'),
                  ('standardized_residual', 'inference.standardized_residual'), ('persistence', 'inference.persistence')],
    }[family]
    names = ['coordinate', 'observed', *(name for name, _ in fields)]
    expressions = ['index_of(observed)', 'observed', *(expression for _, expression in fields)]
    source += 'scored = relation(columns=' + repr(tuple(names)) + ')(' + ','.join(expressions) + ')\n'
    columns = ['relation_field("coordinate", TimestampNsUtc)', *(f'relation_field({json.dumps(name)}, Float64)' for name in names[1:])]
    source += 'answer = sql_program(queries={"observed":"SELECT * FROM scored ORDER BY coordinate"},outputs={"observed":relation_output(columns=[' + ','.join(columns) + '],row_key=("coordinate",),order_by=("coordinate",))})(scored)\nresponse_table(name="observed",source=answer.observed)\n'
    return source
