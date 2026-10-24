"""Keep benchmark comments numerically honest and distinguish partial evidence."""

import json

import pytest

from scripts import feed_options_benchmark as benchmark


def scenario(*, name="broad_english", before=166.76, after=54.73, samples=None, **changes):
    plans = {
        "baseline": dict(
            source_sql_ms=138.01,
            origin_probe_loops=13170,
            article_probe_loops=17601,
            shared_hit_blocks=125223,
            temp_written_blocks=0,
        ),
        "candidate": dict(
            source_sql_ms=30.21,
            origin_probe_loops=500,
            article_probe_loops=661,
            shared_hit_blocks=9162,
            temp_written_blocks=0,
        ),
    }
    return (
        dict(
            rows=1000000,
            case=name,
            concurrent_ingestion=False,
            equivalent=True,
            baseline=dict(wall_ms=before, application_cpu_ms=29.56),
            candidate=dict(wall_ms=after, application_cpu_ms=22.37),
            plan_metrics=plans,
            samples=[
                dict(baseline=dict(wall_ms=b), candidate=dict(wall_ms=a))
                for b, a in (samples or [(before, after)] * 5)
            ],
        )
        | changes
    )


def report(*cases, **changes):
    return (
        dict(
            architecture="aarch64",
            postgres_version="17.11",
            baseline_sha="a" * 40,
            candidate_sha="b" * 40,
            cases=list(cases),
            expected_cases=len(cases),
            completed_at="2026-10-24T10:10:00Z",
        )
        | changes
    )


@pytest.mark.parametrize(
    "before,after,expected",
    [
        (100, 80, "+20.0%"),
        (80, 100, "-25.0%"),
        (100, 100, "+0.0%"),
        (0, 0, "0.0%"),
        (0, 1, "n/a (zero baseline)"),
    ],
)
def test_improvement_sign_and_zero_baseline(before, after, expected):
    assert benchmark.improvement(before, after) == expected


def test_report_has_scan_friendly_latency_and_work_tables_with_signed_deltas():
    output = benchmark.report_markdown(report(scenario()))
    assert "COMPLETE" in output
    assert "1/1 scenarios returned identical public JSON; 5 timed" in output
    assert "| Workload | Before | After | Improvement |" in output
    assert "| 1,000,000 / English, idle | 166.76 ms | 54.73 ms | +67.2% |" in output
    assert "| Origin lookups | 13,170 | 500 | +96.2% |" in output
    assert "| Source SQL replay | 138.01 ms | 30.21 ms | +78.1% |" in output
    assert "| Temporary blocks written | 0 | 0 | 0.0% |" in output
    assert "separately from route timings" in output


def test_noisy_slowdown_keeps_negative_median_delta_and_separate_paired_evidence():
    noisy = scenario(
        name="filtered_combination",
        before=845.57,
        after=1659.31,
        samples=[(1660, 1670), (1657, 1659), (829, 841), (836, 1666), (846, 835)],
    )
    output = benchmark.report_markdown(report(scenario(), noisy))
    assert "845.57 ms | 1,659.31 ms | -96.2%" in output
    assert "Slower medians: inspect these results" in output
    assert "median paired improvement -0.6%" in output
    assert "Inconclusive: paired samples disagree" in output


def test_consistent_slowdown_is_flagged_for_investigation():
    output = benchmark.report_markdown(
        report(scenario(name="filtered_combination", before=100, after=125))
    )
    assert "-25.0%" in output
    assert "Paired samples also show a slowdown; investigate before merging" in output
    assert "Inconclusive" not in output


@pytest.mark.parametrize("completion", [None, "2026-10-24T10:10:00Z"])
def test_incomplete_matrix_never_appears_as_a_complete_run(completion):
    output = benchmark.report_markdown(
        report(scenario(), expected_cases=50, completed_at=completion)
    )
    assert "PARTIAL — unfinished run" in output
    assert "1/50 scenarios" in output


def test_zero_completed_cases_still_produce_a_readable_partial_report():
    output = benchmark.report_markdown(report(expected_cases=50, completed_at=None))
    assert "PARTIAL" in output and "0/50 scenarios" in output
    assert "Source work at" not in output


def test_failed_equality_never_appears_as_complete():
    output = benchmark.report_markdown(report(scenario(equivalent=False)))
    assert "PARTIAL" in output and "0/1 scenarios" in output


def test_full_scenario_details_and_largest_fixture_ingestion_are_preserved(tmp_path):
    cases = [scenario(), scenario(name="generic_broad_english", concurrent_ingestion=True)]
    data = report(
        *cases,
        ingestion=[
            dict(rows=2000, concurrent=True, batches=[dict(articles=20)]),
            dict(rows=1000000, concurrent=True, batches=[dict(articles=20), dict(error="failed")]),
        ],
    )
    benchmark.write_report(tmp_path, data)
    output = (tmp_path / "summary.md").read_text()
    assert "<summary>All 2 route scenarios:" in output
    assert "1,000,000 / English, generic plan, ingestion" in output
    assert "20 additional articles committed; 1 errors" in output
    assert json.loads((tmp_path / "results.json").read_text()) == data
