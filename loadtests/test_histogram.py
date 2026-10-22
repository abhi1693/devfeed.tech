"""Run real Locust statistics checks in the isolated performance environment.

The normal pytest testpaths contain only tests/. Keeping this executable suite
here prevents Locust/gevent from patching the backend unit-test process.
"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import compare
from locust import stats
from policy import gate_failures

DEFAULT_BUCKET = stats.bucket_response_time

# Import the real workload to install the same hook used by each measured run.
import locustfile  # noqa: E402


def measured(response_time, bucket):
    with patch.object(stats, "bucket_response_time", bucket):
        measurements = stats.RequestStats()
        entry = measurements.get("topic", "GET")
        for _ in range(100):
            entry.log(response_time, 100)
        return entry


class HistogramPrecisionChecks(unittest.TestCase):
    def test_workload_installs_fixed_one_millisecond_bins(self):
        self.assertIs(stats.bucket_response_time, locustfile.bucket_response_time)
        self.assertEqual(locustfile.RESPONSE_TIME_BUCKET_MS, 1)
        measurements = stats.RequestStats()
        for route in locustfile.REQUIRED:
            for _ in range(100):
                measurements.log_request("GET", route, 106, 100)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "final.json"
            environment = SimpleNamespace(
                stats=measurements,
                runner=SimpleNamespace(exceptions={}),
                process_exit_code=None,
                parsed_options=SimpleNamespace(
                    final_json=str(destination),
                    include_search=False,
                    minimum_requests=50,
                    max_failure_ratio=0,
                    max_p95_ms=2000,
                ),
            )
            locustfile.quality_gate(environment)
            report = json.loads(destination.read_text())
        self.assertEqual(environment.process_exit_code, 0)
        self.assertEqual(report["response_time_bucket_ms"], 1)
        self.assertEqual(report["gate_failures"], [])
        self.assertTrue(all(entry["p95"] == 106 for entry in report["entries"]))

    def test_subthreshold_increase_does_not_become_a_regression_at_the_bucket_boundary(self):
        base = measured(89, DEFAULT_BUCKET).get_response_time_percentile(0.95)
        rounded_head = measured(106, DEFAULT_BUCKET).get_response_time_percentile(0.95)
        self.assertEqual((base, rounded_head), (89, 110))
        self.assertEqual(compare.classify([base] * 3, [rounded_head] * 3), "REGRESSED")
        precise_head = measured(106, stats.bucket_response_time).get_response_time_percentile(0.95)
        self.assertEqual(precise_head, 106)
        self.assertEqual(compare.classify([base] * 3, [precise_head] * 3), "NO MATERIAL CHANGE")

    def test_real_increase_still_fails_both_unchanged_regression_limits(self):
        base = measured(89, stats.bucket_response_time).get_response_time_percentile(0.95)
        head = measured(120, stats.bucket_response_time).get_response_time_percentile(0.95)
        self.assertGreater(head - base, 20)
        self.assertGreater(head / base, 1.2)
        self.assertEqual(compare.classify([base] * 3, [head] * 3), "REGRESSED")

    def test_real_absolute_smoke_budget_violation_is_not_rounded_down_to_the_limit(self):
        def failures(entry):
            return gate_failures(
                entry,
                [entry],
                minimum=50,
                max_failure_ratio=0,
                max_p95_ms=2000,
                required={"topic"},
            )

        rounded = measured(2010, DEFAULT_BUCKET)
        self.assertEqual(rounded.get_response_time_percentile(0.95), 2000)
        self.assertEqual(failures(rounded), [])
        precise = measured(2010, stats.bucket_response_time)
        self.assertEqual(precise.get_response_time_percentile(0.95), 2010)
        self.assertTrue(failures(precise))
        self.assertTrue(all("2010 exceeds 2000" in message for message in failures(precise)))


if __name__ == "__main__":
    unittest.main()
