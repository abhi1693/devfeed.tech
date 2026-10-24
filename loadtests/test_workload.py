"""Exercise the real load generator in its isolated Locust environment."""

import argparse
import contextlib
import io
import json
import random
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs

import gevent
import locustfile
from gevent.pywsgi import WSGIServer
from locust.env import Environment
from locust.event import Events
from locust.exception import StopUser
from locust.runners import MasterRunner, WorkerRunner


def options(**changes):
    values = dict(
        journey_seed=294,
        measurement_seconds=None,
        warmup_seconds=0.12,
        phase_file=None,
        num_users=2,
        final_json=None,
        include_search=False,
        allow_remote_target=False,
        minimum_requests=1,
        max_failure_ratio=0,
        max_p95_ms=2000,
        search_max_p95_ms=800,
    )
    values.update(changes)
    return SimpleNamespace(**values)


def environment(**changes):
    locustfile.PublicReader.host = "http://127.0.0.1:1"
    return Environment(
        user_classes=[locustfile.PublicReader],
        host="http://127.0.0.1:1",
        events=Events(),
        parsed_options=options(**changes),
        stop_timeout=1,
    )


class FixedJourneys(unittest.TestCase):
    def trace(self, user, count=80):
        trace = []
        for name in set(locustfile.JOURNEYS):
            setattr(user, name, lambda name=name: trace.append(name))
        waits = []
        for _ in range(count):
            user.journey()
            waits.append(user.wait_time())
        return trace, waits

    def test_user_slots_have_repeatable_independent_journey_and_think_streams(self):
        first, second = environment(), environment()
        a0, a1 = locustfile.PublicReader(first), locustfile.PublicReader(first)
        b0, b1 = locustfile.PublicReader(second), locustfile.PublicReader(second)
        self.assertEqual((a0.user_slot, a1.user_slot, b0.user_slot, b1.user_slot), (0, 1, 0, 1))
        expected = self.trace(a0)
        self.trace(a1, 123)
        for _ in range(100):
            random.random()
            b0.resource_random.random()
        self.assertEqual(self.trace(b0), expected)
        self.assertNotEqual(self.trace(b1), expected)
        self.assertTrue(all(0.2 <= wait <= 1 for wait in expected[1]))
        self.assertEqual(set(expected[0]), set(locustfile.JOURNEYS))

    def test_measured_prefix_resets_different_warmup_histories_and_feed_cursors(self):
        a = locustfile.PublicReader(environment())
        b = locustfile.PublicReader(environment())
        bootstrap = ([{"id": "initial"}], "initial-cursor", [{"slug": "topic"}], [{"id": "src"}])
        a.bootstrap_state = b.bootstrap_state = bootstrap
        self.trace(a, 17)
        self.trace(b, 101)
        a.cursor, b.cursor = "different-a", "different-b"
        a.begin_measurement()
        b.begin_measurement()
        self.assertEqual(a.cursor, "initial-cursor")
        self.assertEqual(b.cursor, "initial-cursor")
        self.assertEqual(self.trace(a), self.trace(b))
        alternate = locustfile.PublicReader(environment(journey_seed=295))
        alternate.bootstrap_state = bootstrap
        alternate.begin_measurement()
        self.assertNotEqual(self.trace(a), self.trace(alternate))

    def test_standalone_locust_keeps_its_normal_lifecycle(self):
        env = environment()
        locustfile.configure_measurement(env)
        self.assertFalse(hasattr(env, "_reader_measurement"))
        self.assertEqual(env.events.spawning_complete._handlers, [])
        previous = locustfile.PublicReader(env)
        env.events.test_start.fire(environment=env)
        self.assertEqual(env._reader_users, {})
        self.assertEqual(locustfile.PublicReader(env).user_slot, previous.user_slot)

    def test_controlled_measurement_defaults_to_one_local_user_and_rejects_distributed(self):
        env = environment(measurement_seconds=1, num_users=None)
        locustfile.configure_measurement(env)
        self.assertEqual(env._reader_measurement.report["users"], 1)
        for runner_type in (MasterRunner, WorkerRunner):
            env = environment(measurement_seconds=1)
            env.runner = object.__new__(runner_type)
            env.runner.greenlet = None
            with self.assertRaisesRegex(ValueError, "local load generator"):
                locustfile.configure_measurement(env)
            env.runner = SimpleNamespace(exceptions={})
            with (
                patch.object(locustfile, "gate_failures", return_value=[]),
                self.assertLogs(locustfile.logger, level="ERROR"),
            ):
                locustfile.quality_gate(env)
            self.assertEqual(env.process_exit_code, 1)

    def test_invalid_phase_durations_are_rejected_by_the_cli(self):
        parser = argparse.ArgumentParser()
        locustfile.arguments(parser)
        for value in ("0", "-1", "nan", "inf"):
            with (
                self.subTest(value=value),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                parser.parse_args(["--measurement-seconds", value])
        self.assertEqual(
            parser.parse_args(["--measurement-seconds", "0.2"]).measurement_seconds, 0.2
        )


class MeasurementPhases(unittest.TestCase):
    def test_real_ramp_bootstrap_warmup_and_full_measured_window(self):
        self.run_http_window()

    def test_warmup_http_failure_cannot_be_erased_by_measurement_reset(self):
        self.run_http_window(fail_warmup=True)

    def run_http_window(self, *, fail_warmup=False):
        bootstraps = []
        responses = []
        failed_options = []

        class Reader(locustfile.PublicReader):
            def on_start(self):
                # The second user completes after spawning_complete fires.
                gevent.sleep(0.2 if self.user_slot == 1 else 0.01)
                super().on_start()
                bootstraps.append(time.monotonic())

            def wait_time(self):
                return 0.005

        article = {"id": "1", "slug": "article", "title": "Article"}

        def serve(request, start_response):
            path = request["PATH_INFO"]
            query = parse_qs(request["QUERY_STRING"])
            if path == "/v1/feed":
                payload = {"items": [article], "next_cursor": "page-2"}
            elif path == "/v1/feed/options":
                payload = {"content_types": ["article"], "sources": [{"id": "src"}]}
            elif path in ("/v1/topics", "/v1/sources"):
                payload = [{"id": "src", "slug": "topic"}]
            elif path == "/v1/search":
                payload = {
                    "sections": {
                        key: {"items": []} for key in ("articles", "topics", "sources", "tags")
                    }
                }
            else:
                payload = article
            # Leave a real HTTP request in flight when warmup ends.
            gevent.sleep(0.025 if query.get("diverse") else 0.005)
            status = "200 OK"
            if fail_warmup and path == "/v1/feed/options" and not failed_options:
                status = "503 Service Unavailable"
                failed_options.append(path)
            start_response(status, [("Content-Type", "application/json")])
            return [json.dumps(payload).encode()]

        server = WSGIServer(("127.0.0.1", 0), serve, log=None)
        server.start()
        with tempfile.TemporaryDirectory() as temporary:
            phase_file = Path(temporary) / "measurement.json"
            env = environment(
                measurement_seconds=0.35,
                warmup_seconds=0.12,
                phase_file=str(phase_file),
                final_json=str(Path(temporary) / "final.json"),
            )
            env.host = f"http://127.0.0.1:{server.server_port}"
            env.user_classes = [Reader]
            runner = env.create_local_runner()
            locustfile.configure_measurement(env)
            window = env._reader_measurement
            env.events.request.add_listener(
                lambda **kwargs: responses.append((window.phase, kwargs["name"]))
            )
            reset = env.stats.reset_all
            reset_state = []

            def reset_after_barrier():
                reset_state.append((time.monotonic(), len(window.parked_users), runner.user_count))
                reset()

            env.stats.reset_all = reset_after_barrier
            started = time.monotonic()
            try:
                runner.start(2, spawn_rate=4)
                gevent.with_timeout(5, runner.greenlet.join)
                report = json.loads(phase_file.read_text())
                self.assertEqual(report["phase"], "finished")
                self.assertEqual(report["ready_users"], 2)
                self.assertEqual(reset_state[0][1:], (2, 2))
                self.assertGreaterEqual(reset_state[0][0] - max(bootstraps), 0.12)
                self.assertGreaterEqual(report["warmup_completed_seconds"], 0.12)
                self.assertGreaterEqual(report["measured_seconds"], 0.35)
                self.assertGreaterEqual(time.monotonic() - started, 0.2 + 0.25 + 0.12 + 0.35)
                self.assertLessEqual(
                    datetime.fromisoformat(report["warmup_finished_at"]),
                    datetime.fromisoformat(report["started_at"]),
                )
                measured = [
                    name for phase, name in responses if phase in {"measurement", "stopping"}
                ]
                self.assertEqual(env.stats.total.num_requests, len(measured))
                self.assertTrue(any(phase == "warmup" for phase, _ in responses))
                self.assertGreater(len(measured), 10)
                self.assertEqual(runner.user_count, 0)
                self.assertEqual(env.stats.total.num_failures, 0)
                if fail_warmup:
                    self.assertEqual(window.warmup_failures, 1)
                    with self.assertLogs(locustfile.logger, level="ERROR"):
                        locustfile.quality_gate(env)
                else:
                    locustfile.quality_gate(env)
                self.assertEqual(env.process_exit_code, int(fail_warmup))
            finally:
                runner.quit()
                server.stop()

    def test_failed_bootstrap_or_barrier_stops_without_a_completed_report(self):
        for missing in ("bootstrap", "barrier"):
            with self.subTest(missing=missing):
                env = environment(measurement_seconds=0.01, warmup_seconds=0.01)
                env.runner = SimpleNamespace(quit=lambda: None, exceptions={})
                locustfile.configure_measurement(env)
                window = env._reader_measurement
                if missing == "bootstrap":
                    window.ready = SimpleNamespace(wait=lambda **kwargs: False)
                else:
                    window.ready.set()
                    window.parked = SimpleNamespace(wait=lambda **kwargs: False)
                with self.assertLogs(locustfile.logger, level="ERROR"):
                    window.measure()
                self.assertEqual(window.phase, "failed")
                self.assertIn(missing, window.report["error"])
                self.assertEqual(env.process_exit_code, 1)
                with self.assertLogs(locustfile.logger, level="ERROR"):
                    locustfile.quality_gate(env)
                self.assertEqual(env.process_exit_code, 1)

    def test_external_stop_kills_phase_wait_and_warmup_failures_remain_failures(self):
        env = environment(measurement_seconds=0.1)
        env.runner = SimpleNamespace(quit=lambda: None, exceptions={})
        locustfile.configure_measurement(env)
        window = env._reader_measurement
        window.spawning_complete(2)
        gevent.sleep(0)
        window.stop()
        self.assertTrue(window.greenlet.dead)
        window.phase = "finished"
        window.warmup_failures = 1
        with (
            patch.object(locustfile, "gate_failures", return_value=[]),
            self.assertLogs(locustfile.logger, level="ERROR"),
        ):
            locustfile.quality_gate(env)
        self.assertEqual(env.process_exit_code, 1)
        window.warmup_failures = 0
        env.runner.exceptions = {"warmup": {"msg": "application exception"}}
        with (
            patch.object(locustfile, "gate_failures", return_value=[]),
            self.assertLogs(locustfile.logger, level="ERROR"),
        ):
            locustfile.quality_gate(env)
        self.assertEqual(env.process_exit_code, 1)
        with self.assertRaises(StopUser):
            window.before_journey(SimpleNamespace())


class PhaseReportWrites(unittest.TestCase):
    def test_phase_output_requires_a_distinct_sibling_of_the_explicit_final_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "reports"
            output.mkdir()
            protected = root / "protected.json"
            protected.write_text("protected")
            for final, phase in (
                (None, output / "measurement.json"),
                (output / "final.json", output / ".." / "protected.json"),
                (protected, protected),
            ):
                with self.subTest(final=final, phase=phase), self.assertRaises(ValueError):
                    locustfile.MeasurementWindow(
                        environment(
                            measurement_seconds=1,
                            final_json=str(final) if final else None,
                            phase_file=str(phase),
                        )
                    )
            self.assertEqual(protected.read_text(), "protected")
            self.assertEqual(list(output.iterdir()), [])

    def test_phase_symlink_is_rejected_and_predictable_temporary_symlink_is_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "reports"
            output.mkdir()
            protected = root / "protected.json"
            protected.write_text("protected")
            phase = output / "measurement.json"
            phase.symlink_to(protected)
            env = environment(
                measurement_seconds=1,
                final_json=str(output / "final.json"),
                phase_file=str(phase),
            )
            with self.assertRaisesRegex(ValueError, "regular sibling"):
                locustfile.MeasurementWindow(env)
            self.assertTrue(phase.is_symlink())
            phase.unlink()
            planted = phase.with_suffix(".tmp")
            planted.symlink_to(protected)
            window = locustfile.MeasurementWindow(env)
            window.report["phase"] = "measurement"
            window.publish()
            self.assertEqual(json.loads(phase.read_text())["phase"], "measurement")
            self.assertTrue(planted.is_symlink())
            self.assertEqual(protected.read_text(), "protected")
            self.assertEqual(list(output.glob(".reader-phase-*")), [])

    def test_failed_atomic_replace_preserves_the_report_and_cleans_its_owned_temporary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            phase = root / "measurement.json"
            phase.write_text("previous complete report")
            env = environment(
                measurement_seconds=1,
                final_json=str(root / "final.json"),
                phase_file=str(phase),
            )
            with (
                patch.object(locustfile.os, "replace", side_effect=PermissionError("read only")),
                self.assertRaises(PermissionError),
            ):
                locustfile.MeasurementWindow(env)
            self.assertEqual(phase.read_text(), "previous complete report")
            self.assertEqual(list(root.glob(".reader-phase-*")), [])


if __name__ == "__main__":
    unittest.main()
