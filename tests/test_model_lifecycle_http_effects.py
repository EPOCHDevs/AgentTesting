import unittest

from tools.model_lifecycle_e2e.http_effects import fit_count


class HttpEffectsTests(unittest.TestCase):
    def setUp(self):
        self.terminal = [{"type": "Lifecycle", "operation_type": "job",
                          "operation_name": "owned", "status": "completed"}]
        self.phases = [{"type": "Log", "event_id": phase, "path": "Node:fit",
                        "context": {"model_operation": "fit", "model_fit_phase": phase}}
                       for phase in ("started", "completed")]

    def test_positive_control_and_complete_zero_fit_stream(self):
        events = [dict(self.terminal[0], status="started"), *self.terminal]
        self.assertEqual(fit_count(self.phases, events, "owned", 1)["completed"], 1)
        ordinary = [{"type": "Log", "context": {}, "message": "Execution completed"}]
        self.assertEqual(fit_count(ordinary, self.terminal, "owned", 0)["completed"], 0)

    def test_missing_or_incomplete_stream_is_unknown(self):
        for logs, events in (([], self.terminal), (self.phases, []),
                             (self.phases, [dict(self.terminal[0], status="failed")])):
            with self.assertRaises(AssertionError):
                fit_count(logs, events, "owned", 0)

    def test_duplicate_or_unpaired_boundaries_fail(self):
        for logs in (self.phases + [self.phases[0]], self.phases[:1]):
            with self.assertRaises(AssertionError):
                fit_count(logs, self.terminal, "owned", 1)

    def test_optional_progress_bridge_is_not_an_extra_fit(self):
        progress = [dict(event, type="Progress") for event in self.phases]
        self.assertEqual(fit_count(self.phases + progress, self.terminal, "owned", 1)["completed"], 1)
