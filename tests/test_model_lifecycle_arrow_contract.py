"""Real Arrow IPC controls for the shared local/cloud consumer decoder."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

import pyarrow as pa
import pyarrow.ipc as ipc

from tools.model_lifecycle_e2e.family_consumers import read_arrow_page
from tools.model_lifecycle_e2e.family_cloud import read_answer


FIELDS = ['coordinate', 'observed', 'p0', 'p1']


def table():
    return pa.table({'coordinate': pa.array([1654819200000000000 + index * 86400000000000
                     for index in range(40)], type=pa.timestamp('ns', tz='UTC')),
                     'observed': pa.array([.125] * 40, type=pa.float64()),
                     'p0': pa.array([.25] * 40, type=pa.float64()),
                     'p1': pa.array([.75] * 40, type=pa.float64())})


def encoded(value):
    sink = pa.BufferOutputStream()
    with ipc.new_stream(sink, value.schema) as writer:
        writer.write_table(value)
    return sink.getvalue().to_pybytes()


class ArrowContractTest(unittest.TestCase):
    def test_real_ipc_preserves_utc_ns_float64_and_all_page_witnesses(self):
        value = table()
        with tempfile.TemporaryDirectory() as directory:
            root, rows = Path(directory), []
            for offset, count in [(0, 17), (17, 17), (34, 6)]:
                page, witness = read_arrow_page(encoded(value.slice(offset, count)), family='HMM',
                    fields=FIELDS, offset=offset, row_count=40, evidence=root)
                rows.extend(page)
                self.assertEqual(witness, json.loads((root / f'arrow-page-{offset}-schema.json').read_text()))
                self.assertEqual(witness['row_count'], count)
                self.assertEqual([x['physical_type'] for x in witness['ordered_fields']],
                                 ['timestamp[ns, tz=UTC]', 'double', 'double', 'double'])
                self.assertEqual([x['null_count'] for x in witness['ordered_fields']], [0, 0, 0, 0])
            self.assertEqual(len(rows), 40)
            self.assertEqual(rows[0], [1654819200000000000, .125, .25, .75])
            self.assertEqual(type(rows[0][0]), int)

    def test_value_equivalent_wrong_physical_types_and_nulls_are_refused_before_conversion(self):
        value = table().slice(0, 17)
        coordinates = value.column(0).cast(pa.int64()).to_pylist()
        variants = {
            'integer_coordinate': value.set_column(0, 'coordinate', pa.array(coordinates, type=pa.int64())),
            'wrong_timezone': value.set_column(0, 'coordinate', pa.array(coordinates, type=pa.timestamp('ns', tz='America/New_York'))),
            'missing_timezone': value.set_column(0, 'coordinate', pa.array(coordinates, type=pa.timestamp('ns'))),
            'wrong_unit': value.set_column(0, 'coordinate', pa.array([x // 1000 for x in coordinates], type=pa.timestamp('us', tz='UTC'))),
            'float32_probability': value.set_column(2, 'p0', pa.array([.25] * 17, type=pa.float32())),
            'null_probability': value.set_column(2, 'p0', pa.array([None] + [.25] * 16, type=pa.float64())),
        }
        for name, changed in variants.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with self.assertRaisesRegex(AssertionError, 'physical types|nulls'):
                    read_arrow_page(encoded(changed), family='HMM', fields=FIELDS, offset=0, row_count=40, evidence=root)
                witness = json.loads((root / 'arrow-page-0-schema.json').read_text())
                self.assertEqual(witness['row_count'], 17)
                if name == 'null_probability':
                    self.assertEqual(witness['ordered_fields'][2]['null_count'], 1)

    def test_cloud_reader_uses_shared_decoder_and_retains_17_17_6_page_schemas(self):
        value = table()
        ref = {'table': 'synthetic-table', 'field_ids': FIELDS, 'row_count': 40, 'execution_id': 'synthetic-consumer'}
        class Wire:
            guard = SimpleNamespace(bind_response=lambda _: None, bind_table=lambda *_: None)
            def call(self, method, path):
                if path.endswith('/outputs'):
                    return {'execution_id': 'synthetic-consumer', 'response_id': 'synthetic-response',
                            'request_id': 'synthetic-request', 'response_version': 1}
                return {'primary_execution_id': 'synthetic-consumer', 'response_id': 'synthetic-response',
                        'request_id': 'synthetic-request', 'version': 1,
                        'content': {'outputs': [{'name': 'observed', 'source': ref}]}}
            def post(self, path, body, binary):
                return encoded(value.slice(body['offset'], body['page_size']))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            answer = read_answer(Wire(), SimpleNamespace(family='HMM'), 'synthetic-consumer', root)
            self.assertEqual(len(answer['rows']), 40)
            schemas = json.loads((root / 'arrow-page-schemas.json').read_text())
            self.assertEqual([x['row_count'] for x in schemas], [17, 17, 6])

    def test_forecast_pages_require_registered_int64_horizon_and_float64_outputs(self):
        for family, fields in [('ARIMA', ['horizon', 'forecast', 'forecast_lower', 'forecast_upper']),
                               ('GARCH', ['horizon', 'forecast_variance', 'forecast_volatility'])]:
            value = pa.table({fields[0]: pa.array([1, 2, 5, 21], type=pa.int64()),
                **{name: pa.array([.25] * 4, type=pa.float64()) for name in fields[1:]}})
            for mode, changed in [('positive', value),
                    ('int32_horizon', value.set_column(0, fields[0], pa.array([1, 2, 5, 21], type=pa.int32()))),
                    ('float32_output', value.set_column(1, fields[1], pa.array([.25] * 4, type=pa.float32()))),
                    ('null_output', value.set_column(1, fields[1], pa.array([None, .25, .25, .25], type=pa.float64())))]:
                with self.subTest(family=family, mode=mode), tempfile.TemporaryDirectory() as directory:
                    options = dict(family=family, fields=fields, offset=0, row_count=4, evidence=Path(directory))
                    if mode == 'positive':
                        rows, schema = read_arrow_page(encoded(changed), **options)
                        self.assertEqual([row[0] for row in rows], [1, 2, 5, 21])
                        self.assertEqual(schema['ordered_fields'][0]['physical_type'], 'int64')
                    else:
                        with self.assertRaisesRegex(AssertionError, 'physical types|nulls'):
                            read_arrow_page(encoded(changed), **options)


if __name__ == '__main__':
    unittest.main()
