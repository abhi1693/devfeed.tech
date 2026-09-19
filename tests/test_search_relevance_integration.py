"""Baseline relevance checks against a real Typesense instance."""

import math

import pytest
from search_relevance_cases import CASES, DOCUMENTS

pytestmark = pytest.mark.integration


def dcg(grades):
    return sum((2**grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(grades))


def test_curated_reader_queries_return_expected_articles(search_engine, record_property):
    search_engine.sync("articles", DOCUMENTS, set())

    reciprocal_ranks = []
    ndcgs = []
    top3_success = 0
    top10_success = 0
    for case in CASES:
        hits = search_engine.search(case.query, ("articles",))["articles"]["hits"]
        result_ids = [hit["document"]["id"] for hit in hits]
        relevant = dict(case.expected_relevance)
        ranks = [
            result_ids.index(identifier) + 1 for identifier in relevant if identifier in result_ids
        ]
        reciprocal_ranks.append(1 / min(ranks) if ranks else 0)
        actual_grades = [relevant.get(identifier, 0) for identifier in result_ids[:3]]
        ideal_grades = sorted(relevant.values(), reverse=True)[:3]
        ndcgs.append(dcg(actual_grades) / dcg(ideal_grades) if dcg(ideal_grades) else 0)
        if any(identifier in result_ids[:3] for identifier in relevant):
            top3_success += 1
        if any(identifier in result_ids[:10] for identifier in relevant):
            top10_success += 1
        assert result_ids[:10], case.query
        assert result_ids.index(case.expected_ids[0]) < 3, case.query

    mrr = sum(reciprocal_ranks) / len(reciprocal_ranks)
    mean_ndcg = sum(ndcgs) / len(ndcgs)
    record_property("mrr", round(mrr, 4))
    record_property("ndcg_at_3", round(mean_ndcg, 4))
    record_property("top3_success", f"{top3_success}/{len(CASES)}")
    record_property("top10_success", f"{top10_success}/{len(CASES)}")
    assert top3_success == len(CASES)
    assert top10_success == len(CASES)
    assert mrr >= 0.95
    assert mean_ndcg >= 0.95
