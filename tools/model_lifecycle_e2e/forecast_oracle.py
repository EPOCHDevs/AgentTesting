"""Bounded, read-only checkpoint inspection and independent forecast arithmetic.

Engineering evidence only: this is neither a fit/recovery path nor a public
parameter API. Accept only the selected owned artifact bytes. Native admission
still owns the complete manifest, capability, ABI and compatibility contract.
The v1 wire layout is pinned to the canonical ARIMA/GARCH codecs in Native105.
No service calls or model estimation occur here.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from statistics import NormalDist
import struct

from .native_artifact import ENVELOPE, MAX_BYTES

Vector = tuple[float, ...]
Matrix = tuple[Vector, ...]
MAX_WIDTH = 64
MAX_HORIZON = 128  # Qualification bound, not the public operation's limit.


@dataclass(frozen=True)
class Identity:
    digest: str
    owner: str
    producer: str
    family: str
    fit_cutoff: str


@dataclass(frozen=True)
class Diagnostics:
    log_likelihood: float
    aic: float
    bic: float
    evaluations: int
    restart_index: int
    optimizer: str
    termination: str
    message: str


@dataclass(frozen=True)
class ArimaCheckpoint:
    identity: Identity
    p: int
    d: int
    q: int
    with_constant: bool
    use_state_space: bool
    training_count: int
    final_observation: float
    phi: Vector
    theta: Vector
    constant: float
    sigma2: float
    observations: Vector
    residuals: Vector
    state: Vector
    covariance: Matrix
    transition: Matrix
    observation: Matrix
    noise: Matrix
    diagnostics: Diagnostics


@dataclass(frozen=True)
class GarchCheckpoint:
    identity: Identity
    p: int
    q: int
    distribution: str
    omega: float
    alpha: Vector
    beta: Vector
    mean_return: float
    backcast: float
    degrees_of_freedom: float
    residual_squares: Vector
    variances: Vector
    diagnostics: Diagnostics


@dataclass(frozen=True)
class PointForecast:
    horizon: int
    point: float
    lower: float
    upper: float
    variance: float


@dataclass(frozen=True)
class VarianceForecast:
    horizon: int
    variance: float
    volatility: float


class _Reader:
    def __init__(self, data: bytes, magic: bytes):
        if not data.startswith(magic):
            raise ValueError("Unsupported checkpoint payload format")
        self.data = data
        self.offset = len(magic)

    def take(self, size: int) -> bytes:
        if size < 0 or self.offset + size > len(self.data):
            raise ValueError("Truncated checkpoint payload")
        result = self.data[self.offset:self.offset + size]
        self.offset += size
        return result

    def u32(self) -> int:
        return struct.unpack(">I", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack(">Q", self.take(8))[0]

    def number(self) -> float:
        value = struct.unpack(">d", self.take(8))[0]
        if not math.isfinite(value):
            raise ValueError("Nonfinite checkpoint number")
        return value

    def numbers(self, count: int) -> Vector:
        return tuple(self.number() for _ in range(count))

    def flag(self) -> bool:
        value = self.u32()
        if value not in (0, 1):
            raise ValueError("Invalid checkpoint boolean")
        return bool(value)

    def vector(self) -> Vector:
        width = self.u32()
        if width > MAX_WIDTH:
            raise ValueError("Checkpoint vector exceeds qualification bound")
        return self.numbers(width)

    def matrix(self) -> Matrix:
        rows, cols = self.u32(), self.u32()
        if rows > MAX_WIDTH or cols > MAX_WIDTH:
            raise ValueError("Checkpoint matrix exceeds qualification bound")
        values = self.numbers(rows * cols)
        # The canonical codec writes Armadillo matrices column first.
        return tuple(tuple(values[col * rows + row] for col in range(cols))
                     for row in range(rows))

    def text(self) -> str:
        size = self.u32()
        if size > 4096:
            raise ValueError("Checkpoint string exceeds qualification bound")
        return self.take(size).decode("utf-8")

    def diagnostics(self) -> Diagnostics:
        result = Diagnostics(self.number(), self.number(), self.number(),
                             self.u64(), self.u64(), self.text(), self.text(), self.text())
        if not self.flag() or result.evaluations == 0 or result.restart_index >= 3:
            raise ValueError("Checkpoint lacks converged optimizer evidence")
        if result.optimizer != "SLSQP" or not result.termination or not result.message:
            raise ValueError("Checkpoint optimizer evidence is incomplete")
        if self.offset != len(self.data):
            raise ValueError("Trailing checkpoint bytes")
        return result


def _payload(data: bytes, *, digest: str, owner: str, execution: str,
             family: str) -> tuple[Identity, bytes]:
    if not owner or not execution:
        raise ValueError("Explicit owned fixture identity is required")
    if len(data) > MAX_BYTES or digest != "sha256:" + hashlib.sha256(data).hexdigest():
        raise ValueError("Artifact differs from the selected digest or exceeds the bound")
    offset = len(ENVELOPE)
    if not data.startswith(ENVELOPE) or len(data) < offset + 8:
        raise ValueError("Unsupported or truncated model envelope")
    length = struct.unpack_from(">Q", data, offset)[0]
    offset += 8
    if not length or offset + length >= len(data):
        raise ValueError("Invalid manifest or payload length")
    # Untrusted JSON is inspected only at this boundary; no open mapping escapes.
    manifest = json.loads(data[offset:offset + length])
    if not isinstance(manifest, dict):
        raise ValueError("Invalid artifact manifest")
    expected = {"schema_version": 1, "family": family,
                "implementation_id": "epoch.slsqp." + family.lower(),
                "implementation_schema": 1,
                "payload_format": "epoch.slsqp." + family.lower() + ".binary.v1",
                "owner_user_id": owner, "producer_execution_id": execution}
    if (type(manifest.get("schema_version")) is not int
            or type(manifest.get("implementation_schema")) is not int
            or any(manifest.get(key) != value for key, value in expected.items())):
        raise ValueError("Artifact contract, owner or producer differs from selection")
    cutoff = manifest.get("fit_cutoff")
    if not isinstance(cutoff, str) or not cutoff:
        raise ValueError("Checkpoint requires its recorded fit cutoff")
    return Identity(digest, owner, execution, family, cutoff), data[offset + length:]


def decode_arima(data: bytes, *, digest: str, owner: str,
                 execution: str) -> ArimaCheckpoint:
    identity, payload = _payload(data, digest=digest, owner=owner,
                                 execution=execution, family="ARIMA")
    reader = _Reader(payload, b"EPOCH_ARIMA_FIT_V1\n")
    p, d, q = reader.u32(), reader.u32(), reader.u32()
    if p > 5 or q > 5 or d > 2:
        raise ValueError("Unsupported ARIMA order")
    constant, state_space = reader.flag(), reader.flag()
    count, final = reader.u64(), reader.number()
    phi, theta = reader.numbers(p), reader.numbers(q)
    intercept, sigma2 = reader.number(), reader.number()
    observations, residuals, state = reader.vector(), reader.vector(), reader.vector()
    covariance, transition = reader.matrix(), reader.matrix()
    observation, noise = reader.matrix(), reader.matrix()
    diagnostics = reader.diagnostics()
    if count < 2 or sigma2 <= 0 or state_space != (d > 0):
        raise ValueError("Invalid ARIMA checkpoint count, variance or differencing mode")
    if not constant and intercept != 0:
        raise ValueError("Unexpected ARIMA constant")
    if state_space:
        n = len(state)
        if not n or observations or residuals or len(observation) != 1 or len(observation[0]) != n:
            raise ValueError("Invalid ARIMA state-space checkpoint width")
        if any(len(matrix) != n or any(len(row) != n for row in matrix)
               for matrix in (covariance, transition, noise)):
            raise ValueError("Invalid ARIMA state-space matrix shape")
    elif len(observations) != p or len(residuals) != q or any(
            (state, covariance, transition, observation, noise)):
        raise ValueError("Invalid ARIMA CSS checkpoint widths")
    return ArimaCheckpoint(identity, p, d, q, constant, state_space, count, final,
                           phi, theta, intercept, sigma2, observations, residuals,
                           state, covariance, transition, observation, noise, diagnostics)


def decode_garch(data: bytes, *, digest: str, owner: str,
                 execution: str) -> GarchCheckpoint:
    identity, payload = _payload(data, digest=digest, owner=owner,
                                 execution=execution, family="GARCH")
    reader = _Reader(payload, b"EPOCH_GARCH_FIT_V1\n")
    p, q, distribution = reader.u32(), reader.u32(), reader.u32()
    if not 1 <= p <= 5 or not 1 <= q <= 5 or distribution not in (0, 1):
        raise ValueError("Unsupported GARCH order or distribution")
    omega = reader.number()
    alpha, beta = reader.numbers(p), reader.numbers(q)
    mean, backcast, dof = reader.number(), reader.number(), reader.number()
    residuals, variances = reader.numbers(p), reader.numbers(q)
    diagnostics = reader.diagnostics()
    if (omega <= 0 or any(x < 0 for x in (*alpha, *beta, *residuals))
            or any(x <= 0 for x in variances) or math.fsum((*alpha, *beta)) >= 1
            or backcast <= 0 or (distribution == 1 and dof <= 2)):
        raise ValueError("Invalid GARCH stationary checkpoint state")
    return GarchCheckpoint(identity, p, q, "normal" if distribution == 0 else "student_t",
                           omega, alpha, beta, mean, backcast, dof, residuals,
                           variances, diagnostics)


def _horizon(value: int) -> None:
    if type(value) is not int or not 1 <= value <= MAX_HORIZON:
        raise ValueError("Horizon must be an integer inside the qualification bound")


def _matmul(left: Matrix, right: Matrix) -> Matrix:
    return tuple(tuple(math.fsum(x * y for x, y in zip(row, column))
                       for column in zip(*right)) for row in left)


def arima_future(state: ArimaCheckpoint, horizon: int,
                  confidence: float = 0.95) -> PointForecast:
    """Independent linear state propagation, conditional on exact fitted state."""
    _horizon(horizon)
    if not state.use_state_space or not 0.8 <= confidence <= 0.99:
        raise ValueError("This oracle requires an integrated state-space fit and valid confidence")
    x: Matrix = tuple((value,) for value in state.state)
    covariance = state.covariance
    transpose: Matrix = tuple(zip(*state.transition))
    for _ in range(horizon):
        x = _matmul(state.transition, x)
        propagated = _matmul(_matmul(state.transition, covariance), transpose)
        covariance = tuple(tuple(v + q for v, q in zip(row, noise))
                           for row, noise in zip(propagated, state.noise))
    point = _matmul(state.observation, x)[0][0]
    variance = _matmul(_matmul(state.observation, covariance),
                       tuple(zip(*state.observation)))[0][0]
    if not math.isfinite(point) or not math.isfinite(variance) or variance < 0:
        raise ValueError("Invalid independently propagated forecast")
    spread = NormalDist().inv_cdf((1 + confidence) / 2) * math.sqrt(variance)
    return PointForecast(horizon, point, point - spread, point + spread, variance)


def garch_future(state: GarchCheckpoint, horizon: int) -> VarianceForecast:
    """Closed-form GARCH(1,1) recurrence from the saved terminal histories."""
    _horizon(horizon)
    if state.p != 1 or state.q != 1 or state.distribution != "normal":
        raise ValueError("This qualification oracle is frozen to normal GARCH(1,1)")
    value = max(1e-12, state.omega + state.alpha[0] * state.residual_squares[0]
                + state.beta[0] * state.variances[0])
    for _ in range(1, horizon):
        value = max(1e-12, state.omega + (state.alpha[0] + state.beta[0]) * value)
    if not math.isfinite(value):
        raise ValueError("Nonfinite independent variance forecast")
    return VarianceForecast(horizon, value, math.sqrt(value))
