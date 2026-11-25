"""Synthetic byte/math controls; no Native fit, server or family verdict."""
import hashlib
import json
import math
from statistics import NormalDist
import struct
import unittest

from tools.model_lifecycle_e2e.forecast_oracle import (
    arima_future, decode_arima, decode_garch, garch_future,
)
from tools.model_lifecycle_e2e.native_artifact import ENVELOPE


def u32(value):
    return struct.pack(">I", value)


def number(value):
    return struct.pack(">d", value)


def text(value):
    data = value.encode()
    return u32(len(data)) + data


def vector(values):
    return u32(len(values)) + b"".join(number(x) for x in values)


def matrix(values):
    rows = len(values)
    cols = len(values[0]) if rows else 0
    return u32(rows) + u32(cols) + b"".join(
        number(values[r][c]) for c in range(cols) for r in range(rows))


def diagnostics(*, converged=True, restart=1):
    return (b"".join(number(x) for x in (-12.0, 30.0, 40.0))
            + struct.pack(">QQ", 25, restart) + text("SLSQP")
            + text("FToleranceReached") + text("fixture converged") + u32(int(converged)))


def artifact(family, payload, **overrides):
    manifest = {"schema_version": 1, "family": family,
                "implementation_id": "epoch.slsqp." + family.lower(),
                "implementation_schema": 1,
                "payload_format": "epoch.slsqp." + family.lower() + ".binary.v1",
                "owner_user_id": "fixture", "producer_execution_id": "fit-run",
                "fit_cutoff": "2022-06-09T00:00:00Z", **overrides}
    body = json.dumps(manifest, separators=(",", ":")).encode()
    return ENVELOPE + struct.pack(">Q", len(body)) + body + payload


def garch(*, omega=0.1, alpha=0.2, beta=0.7, mean=0.37,
          residual_squared=4.0, variance=5.0, converged=True, restart=1):
    payload = (b"EPOCH_GARCH_FIT_V1\n" + u32(1) + u32(1) + u32(0)
               + b"".join(number(x) for x in
                          (omega, alpha, beta, mean, 1.5, 0.0, residual_squared, variance))
               + diagnostics(converged=converged, restart=restart))
    return artifact("GARCH", payload)


def arima(*, converged=True, transition=((1.0, 1.0), (0.0, 1.0))):
    payload = (b"EPOCH_ARIMA_FIT_V1\n" + b"".join(u32(x) for x in (0, 1, 0, 1, 1))
               + struct.pack(">Q", 160) + number(100.0) + number(0.5) + number(0.01)
               + vector(()) + vector(()) + vector((100.0, 0.5))
               + matrix(((0.2, 0.0), (0.0, 0.0))) + matrix(transition)
               + matrix(((1.0, 0.0),)) + matrix(((0.01, 0.0), (0.0, 0.0)))
               + diagnostics(converged=converged))
    return artifact("ARIMA", payload)


class ForecastOracleTest(unittest.TestCase):
    def decode(self, family, data, **overrides):
        args = {"digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                "owner": "fixture", "execution": "fit-run", **overrides}
        return (decode_arima if family == "ARIMA" else decode_garch)(data, **args)

    def test_actual_byte_order_and_nonzero_training_mean_are_retained(self):
        state = self.decode("GARCH", garch())
        self.assertEqual((state.omega, state.alpha, state.beta), (0.1, (0.2,), (0.7,)))
        self.assertEqual(state.mean_return, 0.37)
        self.assertEqual(state.residual_squares, (4.0,))
        self.assertEqual(state.diagnostics.restart_index, 1)
        self.assertEqual(state.identity.fit_cutoff, "2022-06-09T00:00:00Z")

    def test_garch_recurrence_matches_independent_geometric_solution(self):
        state = self.decode("GARCH", garch())
        first, persistence = 4.4, 0.9
        unconditional = 0.1 / (1 - persistence)
        for h in (1, 2, 5, 21):
            result = garch_future(state, h)
            expected = unconditional + (first - unconditional) * persistence ** (h - 1)
            self.assertAlmostEqual(result.variance, expected, places=12)
            self.assertAlmostEqual(result.volatility, math.sqrt(expected), places=12)

    def test_arima_column_major_checkpoint_and_nonzero_covariance(self):
        state = self.decode("ARIMA", arima())
        self.assertEqual(state.transition, ((1.0, 1.0), (0.0, 1.0)))
        z = NormalDist().inv_cdf(0.975)
        for h in (1, 2, 5, 21):
            result = arima_future(state, h)
            expected, variance = 100 + 0.5 * h, 0.2 + 0.01 * h
            self.assertAlmostEqual(result.point, expected, places=12)
            self.assertAlmostEqual(result.variance, variance, places=12)
            self.assertAlmostEqual(result.lower, expected - z * math.sqrt(variance), places=12)
            self.assertAlmostEqual(result.upper, expected + z * math.sqrt(variance), places=12)

    def test_owner_producer_and_digest_are_required_before_state_inspection(self):
        for family, data in (("ARIMA", arima()), ("GARCH", garch())):
            for args in ({"owner": "another"}, {"execution": "another"},
                         {"owner": ""}, {"digest": "sha256:" + "0" * 64}):
                with self.subTest(family=family, args=args), self.assertRaises(ValueError):
                    self.decode(family, data, **args)

    def test_truncation_or_trailing_bytes_do_not_become_a_reconstructed_fit(self):
        for family, data in (("ARIMA", arima()), ("GARCH", garch())):
            for changed in (b"", data[:16], data[:-1], data + b"extra"):
                with self.subTest(family=family), self.assertRaises(ValueError):
                    self.decode(family, changed)

    def test_nonfinite_unstable_or_unconverged_state_is_refused(self):
        for kwargs in ({"omega": math.nan}, {"omega": -1}, {"variance": 0},
                       {"residual_squared": -1}, {"alpha": 0.7, "beta": 0.8},
                       {"converged": False}, {"restart": 3}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.decode("GARCH", garch(**kwargs))
        with self.assertRaises(ValueError):
            self.decode("ARIMA", arima(converged=False))

    def test_horizon_bound_is_distinct_from_the_public_operation_limit(self):
        a = self.decode("ARIMA", arima())
        g = self.decode("GARCH", garch())
        for h in (0, -1, True, 1.5, 129):
            for state, function in ((a, arima_future), (g, garch_future)):
                with self.subTest(h=h), self.assertRaises(ValueError):
                    function(state, h)


if __name__ == "__main__":
    unittest.main()
