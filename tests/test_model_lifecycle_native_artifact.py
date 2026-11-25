import hashlib
import json
import struct
import unittest

from tools.model_lifecycle_e2e.native_artifact import ENVELOPE, PAYLOAD, decode_owned_ols


def artifact(*, count=4, width=1, flag=1, intercept=3.0, baseline=6.0, slope=2.0, trailing=b"", owner="fixture"):
    manifest = {"schema_version": 2, "family": "OLS", "implementation_id": "epoch.eigen.ols",
                "implementation_schema": 1, "payload_format": "epoch.eigen.ols.binary.v1",
                "owner_user_id": owner, "producer_execution_id": "fit-run",
                "deterministic_solver": {"solver_id": "epoch.eigen.col_piv_householder_qr.v1"},
                "supervised_training": {"selected_row_count": count}}
    encoded = json.dumps(manifest, separators=(",", ":")).encode()
    return ENVELOPE + struct.pack(">Q", len(encoded)) + encoded + PAYLOAD + struct.pack(">QQQddd", flag, width, count, intercept, baseline, slope) + trailing


class NativeArtifactAuditTest(unittest.TestCase):
    def decode(self, data, *, digest=None, owner="fixture", execution="fit-run"):
        return decode_owned_ols(data, digest=digest or "sha256:" + hashlib.sha256(data).hexdigest(),
                                owner=owner, execution=execution)

    def test_exact_binary64_state_and_original_producer(self):
        state = self.decode(artifact())
        self.assertEqual((state.intercept, state.slopes, state.baseline, state.training_count),
                         (3.0, (2.0,), 6.0, 4))
        with self.assertRaisesRegex(ValueError, "owner or original producer"):
            self.decode(artifact(), execution="later-run")
        with self.assertRaisesRegex(ValueError, "owner or original producer"):
            self.decode(artifact(), owner="another-owner")

    def test_mutated_or_missing_bytes_never_reconstruct_a_fit(self):
        data = artifact()
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        for changed in (data[:-1], data + b"x", data[:-1] + b"x", b""):
            with self.assertRaisesRegex(ValueError, "selected digest"):
                self.decode(changed, digest=digest)

    def test_invalid_dimensions_counts_or_nonfinite_state_fail(self):
        for kwargs in ({"width": 2}, {"width": 0}, {"flag": 2}, {"count": 1},
                       {"slope": float("nan")}, {"baseline": float("inf")},
                       {"flag": 0, "intercept": 3.0}, {"trailing": b"extra"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.decode(artifact(**kwargs))

    def test_explicit_scope_is_required(self):
        for kwargs in ({"owner": ""}, {"execution": ""}):
            with self.assertRaisesRegex(ValueError, "Explicit disposable fixture"):
                self.decode(artifact(), **kwargs)


if __name__ == "__main__":
    unittest.main()
