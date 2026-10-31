"""Large supervised catalogs stay usable without overflowing inference input."""

import json
import random
import uuid
from itertools import islice
from types import SimpleNamespace

import pytest
from devfeed_aggregator import analysis_tasks
from devfeed_core import analysis
from devfeed_core.models import Article, ArticleAnalysisJob, ArticleOrigin, Source, Tag, Topic
from devfeed_core.taxonomy import classify
from devfeed_core.urls import fingerprint

from tests.support.source_readiness import configuration


def entry(name, **values):
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "slug": name.lower(),
        "aliases": [],
        "keywords": [],
        **values,
    }


def test_candidates_rank_names_aliases_keywords_and_word_boundaries():
    taxonomy = {"topics": [entry(f"A filler {i}") for i in range(600)], "tags": []}
    relevant = [
        entry("Zulu", aliases=["autonomous-assistant"]),
        entry("Widgets", keywords=["agent tools"]),
        entry("C++"),
    ]
    unrelated = entry("Go")
    taxonomy["topics"] += [*relevant, unrelated]
    snapshot = {
        "title": "Autonomous assistant agent tools in C++",
        "text": "Google's engineering guide.",
    }
    candidates = analysis.analysis_candidates(taxonomy, snapshot)
    selected = {item["id"] for item in candidates["topics"]}
    assert len(selected) == len(relevant) + 8
    assert all(item["id"] in selected for item in relevant)
    assert unrelated["id"] not in selected  # Go must not match Google.
    assert (
        analysis.analysis_candidates(
            {"topics": list(reversed(taxonomy["topics"])), "tags": []}, snapshot
        )
        == candidates
    )


def test_shared_alias_keeps_both_ai_candidates_without_fallback_assignment():
    shared = [
        entry("Continuous Delivery", aliases=["CD"]),
        entry("Continuous Deployment", aliases=["CD"]),
    ]
    taxonomy = {"topics": [entry(f"A filler {i}") for i in range(600)] + shared, "tags": []}
    candidates = analysis.analysis_candidates(taxonomy, {"title": "CD workflows"})
    assert {item["id"] for item in shared} <= {item["id"] for item in candidates["topics"]}
    topics = [Topic(**{**item, "status": "active"}) for item in shared]
    assert classify("CD workflows", "", [], topics) == []


def test_topic_descriptions_reach_inference_as_bounded_semantic_context():
    described = entry(
        "Career advice",
        description="Guidance on interviewing, resumes, and professional growth.",
    )
    fallback = entry("A unrelated catalog item")
    taxonomy = {
        "topics": [fallback, described, *[entry(f"Filler {i}") for i in range(20)]],
        "tags": [],
    }
    candidates = analysis.analysis_candidates(
        taxonomy,
        {"title": "Interviewing and resumes", "text": "Improve your professional growth."},
    )
    assert candidates["topics"][0]["id"] == described["id"]
    assert candidates["topics"][0]["description"] == described["description"]
    assert "description" not in candidates["topics"][1]


def test_catalog_context_normalizes_whitespace_and_bounds_utf8_without_mutating_catalog():
    description = "  Design\n  iteration " + "設計" * 5000
    generated = " \tUser-centered\nprototyping " + "é" * 5000
    topic = entry("iteration", description=description, ai_description=generated)
    prompt_item = analysis._prompt_catalog_item(topic)
    for field, prefix in (
        ("description", "Design iteration"),
        ("ai_description", "User-centered prototyping"),
    ):
        assert prompt_item[field].startswith(prefix)
        assert len(prompt_item[field].encode()) <= analysis.TOPIC_CONTEXT_BYTES
        assert "�" not in prompt_item[field]
    assert topic["description"] == description
    assert topic["ai_description"] == generated


def test_catalog_context_keeps_ai_definition_when_description_is_blank():
    topic = entry("iteration", description=" \n ", ai_description="Programming loop execution.")
    prompt_item = analysis._prompt_catalog_item(topic)
    assert "description" not in prompt_item
    assert prompt_item["ai_description"] == topic["ai_description"]


def test_prompt_budget_counts_unicode_article_and_long_aliases():
    taxonomy = {
        field: [
            entry(
                f"Subject {i}",
                aliases=["編程" * 30] * 20,
                description="文脈 " * 5000,
                ai_description="Bounded semantic definitions. " * 1000,
            )
            for i in range(650)
        ]
        for field in ("topics", "tags")
    }
    snapshot = {"title": "Subject 649", "text": "工程 " * 12_000}
    candidates = analysis.analysis_candidates(taxonomy, snapshot)
    assert len(analysis.analysis_prompt(snapshot, candidates).encode()) <= 240_000
    assert candidates["topics"] and candidates["tags"]
    assert sum(map(len, candidates.values())) < 1000


def test_candidate_budget_does_not_build_the_legacy_aggregate_prompt(monkeypatch):
    def legacy_prompt(*_):
        pytest.fail("Production candidate ranking must not build the legacy giant prompt")

    monkeypatch.setattr(analysis, "analysis_prompt", legacy_prompt)
    taxonomy = {"topics": [entry("Rust")], "tags": []}
    candidates = analysis.analysis_candidates(taxonomy, {"title": "Rust release", "text": "Rust"})
    assert candidates["topics"][0]["id"] == taxonomy["topics"][0]["id"]


@pytest.mark.integration
def test_large_catalog_runs_analysis_and_manual_classification(database, monkeypatch):
    with database.begin() as session:
        topics = [
            Topic(
                name=f"Catalog topic {i:04}", slug=f"topic-{i}", kind="technology", status="active"
            )
            for i in range(1300)
        ]
        relevant = Topic(
            name="AI agents",
            slug="ai-agents",
            kind="technology",
            status="active",
            aliases=["Autonomous assistants"],
        )
        hidden = Topic(
            name="Pending topic", slug="pending-topic", kind="technology", status="proposed"
        )
        source = Source(
            name="Publisher",
            feed_url="https://example.com/feed",
            source_type="publisher",
            approval_status="approved",
            enabled=True,
        )
        article = Article(
            title="Building autonomous assistants",
            canonical_url="https://example.com/guide",
            url_hash=fingerprint("https://example.com/guide"),
            summary="Autonomous assistants use tools to solve engineering tasks. " * 5,
        )
        session.add_all(
            [
                *topics,
                relevant,
                hidden,
                source,
                article,
                *[Tag(name=f"Tag {i}", slug=f"tag-{i}") for i in range(520)],
            ]
        )
        session.flush()
        session.add(
            ArticleOrigin(
                article_id=article.id,
                source_id=source.id,
                entry_key="guide",
                original_url=article.canonical_url,
            )
        )
        session.flush()
        article_id, relevant_id = article.id, relevant.id
        job_id = analysis.request_analysis(session, article_id).id
        full = analysis.catalog(session)
        assert len(full["topics"]) == 1301 and len(full["tags"]) == 520
        assert str(hidden.id) not in {item["id"] for item in full["topics"]}

    calls = []

    def complete(prompt, schema):
        assert len(prompt.encode()) <= 240_000
        assert len(prompt.encode()) + len(json.dumps(schema).encode()) <= 250_000
        data = json.loads(prompt.rsplit("\n", 1)[1])
        fields = set(schema["properties"]) - {"outcome", "reason"}
        calls.append(fields)
        if fields == {"topics"}:
            assert set(data["catalog"]) == {"topics"}
            assert str(relevant_id) in {item["id"] for item in data["catalog"]["topics"]}
        elif fields == {"tags"}:
            assert set(data["catalog"]) == {"tags"}
        else:
            assert "catalog" not in data
        values = {
            "developer_relevance": "relevant",
            "language": "en",
            "content_type": "tutorial",
            "content_format": "article",
            "ai_summary": "A guide to building autonomous assistants.",
            "ai_title": None,
            "title_evidence": None,
            "page_kind": "article",
            "ai_description": "A guide to autonomous assistants.",
            "topics": [
                {
                    "topic_id": str(relevant_id),
                    "role": "primary",
                    "relevance": 0.9,
                    "evidence": "Autonomous assistants",
                }
            ],
            "tags": [],
        }
        return {
            "outcome": "ready",
            "reason": "Supported by the supplied source.",
            **{field: values[field] for field in fields},
        }

    monkeypatch.setattr(analysis_tasks, "session_factory", lambda: database)
    monkeypatch.setattr(
        analysis_tasks,
        "get_settings",
        lambda: SimpleNamespace(
            ai_enabled=True, full_automation=False, codex_model="fixture-model"
        ),
    )
    monkeypatch.setattr(analysis_tasks, "CodexClient", lambda _: SimpleNamespace(complete=complete))
    analysis_tasks._analyze(job_id)
    assert calls == [
        {"developer_relevance"},
        {"page_kind"},
        {"language"},
        {"content_type"},
        {"content_format"},
        {"topics"},
        {"tags"},
        {"ai_summary"},
        {"ai_description"},
    ]
    with database.begin() as session:
        job = session.get(ArticleAnalysisJob, job_id)
        assert job.status == "succeeded" and job.outcome == "applied", job.error
        assert len(job.catalog_snapshot["topics"]) == 9
        article = session.get(Article, article_id)
        assert article.publication_status == "unpublished" and article.review_status == "pending"
        body = analysis.ManualClassification(
            developer_relevance="relevant",
            language="en",
            content_type="tutorial",
            content_format="article",
            topics=[
                {
                    "topic_id": relevant_id,
                    "role": "primary",
                    "relevance": 1,
                    "evidence": "Autonomous assistants",
                }
            ],
            tags=[],
        )
        analysis.classify_manually(session, article_id, body)
        assert article.classification_provenance["origin"] == "manual"


def test_indexed_scores_preserve_overlap_boundaries_and_catalog_edits():
    items = [
        entry("Go", aliases=["GO", "go lang", "lang"], keywords=["go", "go lang"]),
        entry("C++", aliases=["C#", "c++"], keywords=["tools", "agent tools"]),
        entry("CD", aliases=["continuous delivery", "shared"]),
        entry("Other", aliases=["shared", "delivery"]),
        entry("工程", aliases=["ＡＩ", "foo_bar"]),
        entry("...", aliases=["", "---"]),
    ]
    snapshots = [
        {},
        {"title": "GO lang and Google", "text": "Go lang go lang"},
        {"title": "C++ and C#", "source_summary": "agent tools tools"},
        {"text": "continuous delivery shared 工程 ＡＩ foo_bar"},
    ]
    for snapshot in snapshots:
        assert analysis.candidate_scores(items, snapshot) == [
            analysis.candidate_score(item, snapshot) for item in items
        ]
    before = analysis.candidate_scores(items, {"title": "new alias"})
    items[0]["aliases"].append("new alias")
    after = analysis.candidate_scores(items, {"title": "new alias"})
    assert after[0] > before[0]
    assert after == [analysis.candidate_score(item, {"title": "new alias"}) for item in items]


def test_indexed_scores_match_reference_over_generated_catalog():
    import random

    rng = random.Random(42)
    vocabulary = ["go", "golang", "C++", "C#", "工具", "AI", "ai tools", "foo_bar", "x-y"]
    items = [
        entry(
            rng.choice(vocabulary),
            aliases=rng.choices(vocabulary, k=3),
            keywords=rng.choices(vocabulary, k=4),
        )
        for _ in range(150)
    ]
    for _ in range(40):
        snapshot = {
            field: " ".join(rng.choices(vocabulary, k=20))
            for field in ("title", "source_summary", "text")
        }
        assert analysis.candidate_scores(items, snapshot) == [
            analysis.candidate_score(item, snapshot) for item in items
        ]


def test_description_retrieval_cache_tracks_edits_and_catalog_order():
    items = [entry("Alpha", description="Databases indexing"), entry("Beta")]
    snapshot = {"title": "Databases indexing"}
    before = analysis.candidate_scores(items, snapshot)
    assert before[0] > 0 and before[1] == 0
    assert analysis.candidate_scores(list(reversed(items)), snapshot) == list(reversed(before))
    items[0]["description"] = None
    items[1]["ai_description"] = "Databases indexing"
    assert analysis.candidate_scores(items, snapshot) == list(reversed(before))
    items[1]["ai_description"] = None
    assert analysis.candidate_scores(items, snapshot) == [0, 0]


def test_production_sized_catalog_reuses_normalized_identities():
    # The production catalog exceeds the former 32,768-entry cache. Sequential
    # passes used to evict every entry before it could be reused.
    analysis.candidate_terms.cache_clear()
    items = [entry(f"Catalog identity {i}") for i in range(38000)]
    snapshot = {"title": "Catalog identity 37999"}
    before = analysis.candidate_scores(items, snapshot)
    misses = analysis.candidate_terms.cache_info().misses
    assert analysis.candidate_scores(items, snapshot) == before
    assert analysis.candidate_terms.cache_info().misses == misses


def test_tick_ranking_reuses_inputs_and_invalidates_changed_content_catalog_and_limits(monkeypatch):
    from devfeed_core.config import get_settings

    taxonomy = {"topics": [entry("Angular"), entry("React"), entry("Other")], "tags": []}
    snapshot = {"title": "Angular routing"}
    expected = analysis.analysis_candidates(taxonomy, snapshot)
    scores = analysis._candidate_scores
    calls = []

    def count(*args):
        calls.append(True)
        return scores(*args)

    monkeypatch.setattr(analysis, "_candidate_scores", count)
    with analysis.reuse_candidates():
        assert analysis.analysis_candidates(taxonomy, snapshot) == expected
        assert analysis.analysis_candidates(taxonomy, dict(snapshot)) == expected
        assert len(calls) == 1
        changed = {"title": "React routing"}
        assert analysis.analysis_candidates(taxonomy, changed)["topics"][0]["name"] == "React"
        monkeypatch.setattr(get_settings(), "analysis_fallback_candidates", 0)
        assert len(analysis.analysis_candidates(taxonomy, changed)["topics"]) == 1
        updated = {"topics": [entry("React", aliases=["Angular"])], "tags": []}
        assert analysis.analysis_candidates(updated, snapshot)["topics"] == updated["topics"]
        assert len(calls) == 4
    analysis.analysis_candidates(taxonomy, snapshot)
    assert len(calls) == 5  # Tick results do not leak into another scope.


def test_tick_ranking_releases_old_article_results(monkeypatch):
    taxonomy = {"topics": [entry("Angular")], "tags": []}
    scores = analysis._candidate_scores
    calls = []

    def count(*args):
        calls.append(True)
        return scores(*args)

    monkeypatch.setattr(analysis, "_candidate_scores", count)
    snapshots = [{"title": f"Angular routing {index}"} for index in range(10)]
    with analysis.reuse_candidates():
        original = analysis.analysis_candidates(taxonomy, snapshots[0])
        for snapshot in snapshots[1:]:
            analysis.analysis_candidates(taxonomy, snapshot)
        analysis.analysis_candidates(taxonomy, snapshots[-1])
        assert len(calls) == 10  # Recent results stay available to locked checks.
        assert analysis.analysis_candidates(taxonomy, snapshots[0]) == original
        assert len(calls) == 11  # Older results are discarded without changing selection.


def test_worker_batches_reuse_one_prepared_generation_and_release_replaced_indexes(monkeypatch):
    import gc
    import weakref

    references = []
    original = analysis._PreparedCatalog

    class TrackedCatalog(original):
        def __init__(self, taxonomy):
            super().__init__(taxonomy)
            references.append(weakref.ref(self))

    monkeypatch.setattr(analysis, "_PreparedCatalog", TrackedCatalog)
    taxonomy = {"topics": [entry("Angular"), entry("React")], "tags": []}
    with analysis.reuse_candidates():
        for number in range(3):
            with analysis.reuse_candidates(reuse_existing=True):
                result = analysis.analysis_candidates(taxonomy, {"title": f"React {number}"})
                assert result["topics"][0]["name"] == "React"
        assert len(references) == 1
        taxonomy = {"topics": [entry("Vue")], "tags": []}
        with analysis.reuse_candidates(reuse_existing=True):
            result = analysis.analysis_candidates(taxonomy, {"title": "Vue"})
            assert result["topics"][0]["name"] == "Vue"
        gc.collect()
        assert len(references) == 2
        assert references[0]() is None and references[1]() is not None
    gc.collect()
    assert all(reference() is None for reference in references)


def test_prepared_catalog_indexes_are_released_after_scope_and_cancellation(monkeypatch):
    import gc
    import weakref

    prepared = []
    original = analysis._PreparedCatalog

    class TrackedCatalog(original):
        def __init__(self, taxonomy):
            super().__init__(taxonomy)
            prepared.append(weakref.ref(self))

    monkeypatch.setattr(analysis, "_PreparedCatalog", TrackedCatalog)
    for generation in range(12):
        taxonomy = {
            "topics": [entry(f"Generation {generation} topic {index}") for index in range(200)],
            "tags": [],
        }
        with pytest.raises(RuntimeError), analysis.reuse_candidates():
            first = analysis.analysis_candidates(taxonomy, {"title": "topic"})
            assert analysis.analysis_candidates(taxonomy, {"title": "topic"}) == first
            assert prepared[-1]() is not None
            raise RuntimeError("cancelled tick")
        gc.collect()
        assert all(item() is None for item in prepared)
    # Complete tries and description postings must not retain prior generations
    # independently of the prepared catalog's explicit lifetime.
    assert not hasattr(analysis.candidate_index, "cache_info")
    assert not hasattr(analysis.candidate_description_index, "cache_info")


def test_full_topic_candidate_quota_still_allows_independently_ranked_tag_evidence(monkeypatch):
    settings = configuration(analysis_max_candidates=1, analysis_fallback_candidates=0)
    monkeypatch.setattr(analysis, "get_settings", lambda: settings)
    taxonomy = {
        "topics": [entry(f"A topic {i}", aliases=["Routing correctness"]) for i in range(3)],
        "tags": [entry("Z routing tag", aliases=["Routing correctness"])],
    }
    selected = analysis.analysis_candidates(taxonomy, {"title": "Routing correctness"})
    assert [[row["id"] for row in selected[field]] for field in ("topics", "tags")] == [
        [taxonomy["topics"][0]["id"]],
        [taxonomy["tags"][0]["id"]],
    ]


@pytest.mark.parametrize("seed", range(20))
def test_sparse_candidate_order_matches_full_score_sort_for_ties_categories_and_zeroes(seed):
    randomizer = random.Random(seed)
    taxonomy = {"topics": [], "tags": []}
    for number in range(60):
        field = randomizer.choice(("topics", "tags"))
        taxonomy[field].append(
            entry(
                randomizer.choice(("Angular", "angular", "C++", "Écosystème", "Zulu")),
                id=str(uuid.UUID(int=number + 1)),
            )
        )
    prepared = analysis._PreparedCatalog(taxonomy)
    scores = [randomizer.choice((0, 0, 0, 0.25, 1, 1, 7)) for _ in prepared.entries]
    expected = sorted(range(len(scores)), key=lambda i: (-scores[i], *prepared.sort_keys[i]))
    assert list(prepared.ranked(scores)) == expected
    assert list(prepared.ranked([0] * len(scores))) == sorted(
        range(len(scores)),
        key=prepared.sort_keys.__getitem__,
    )


def test_sparse_ranking_does_not_sort_the_zero_score_catalogue_for_each_article():
    taxonomy = {"topics": [entry(f"Subject {index:05}") for index in range(2000)], "tags": []}
    prepared = analysis._PreparedCatalog(taxonomy)

    class TrackedKeys(list):
        def __init__(self, values):
            super().__init__(values)
            self.reads = 0

        def __getitem__(self, index):
            self.reads += 1
            return super().__getitem__(index)

    prepared.sort_keys = TrackedKeys(prepared.sort_keys)
    scores = [0] * len(prepared.entries)
    scores[-1] = 1
    assert list(islice(prepared.ranked(scores), 3)) == [1999, 0, 1]
    assert prepared.sort_keys.reads == 1


def test_prepared_catalogue_owns_terms_without_filling_the_global_vocabulary_cache():
    taxonomy = {
        "topics": [entry(f"Prepared vocabulary {index}") for index in range(100)],
        "tags": [entry("Other identity")],
    }
    before = analysis.candidate_terms.cache_info()
    prepared = analysis._PreparedCatalog(taxonomy)
    assert analysis.candidate_terms.cache_info() == before
    snapshot = {"title": "Prepared vocabulary 99 and Other identity"}
    root, frequency, postings = prepared.indexes
    scores = analysis._candidate_scores(root, frequency, postings, len(prepared.entries), snapshot)
    entries = [item for _, item in prepared.entries]
    assert scores == analysis.candidate_scores(entries, snapshot)
    assert analysis.candidate_terms.cache_info().currsize >= before.currsize


def test_candidate_indexes_accept_streamed_term_groups_and_descriptions():
    terms = [(frozenset({" c++ "}), frozenset()), (frozenset({" python "}), frozenset())]
    assert analysis.candidate_index(iter(terms)) == analysis.candidate_index(terms)
    descriptions = [("Python concurrency", None), (None, "Python containers")]
    assert analysis.candidate_description_index(iter(descriptions)) == (
        {"python": 2, "concurrency": 1, "containers": 1},
        {"python": [0, 1], "concurrency": [0], "containers": [1]},
    )


def test_prepared_prompt_memoization_bounds_entries_and_rebuilds_evicted_values_exactly():
    taxonomy = {"topics": [entry(f"Prompt {i}") for i in range(2200)], "tags": []}
    prepared = analysis._PreparedCatalog(taxonomy)
    first = prepared.prompt_item(0)
    for index in range(len(prepared.entries)):
        value, size = prepared.prompt_item(index)
        assert value == analysis._prompt_catalog_item(taxonomy["topics"][index])
        assert size == len(json.dumps(value, ensure_ascii=False).encode()) + 2
    assert len(prepared.prompt_items) == analysis.CANDIDATE_PROMPT_CACHE_ENTRIES
    assert prepared.prompt_bytes == sum(size for _, size in prepared.prompt_items.values())
    assert prepared.prompt_bytes <= analysis.CANDIDATE_PROMPT_CACHE_BYTES
    assert 0 not in prepared.prompt_items
    assert prepared.prompt_item(0) == first


def test_retained_ranking_bounds_prompt_bytes_and_results_across_different_articles(monkeypatch):
    monkeypatch.setattr(analysis, "CANDIDATE_PROMPT_CACHE_ENTRIES", 8)
    monkeypatch.setattr(analysis, "CANDIDATE_PROMPT_CACHE_BYTES", 1024)
    settings = configuration(analysis_max_candidates=1, analysis_fallback_candidates=0)
    monkeypatch.setattr(analysis, "get_settings", lambda: settings)
    taxonomy = {
        "topics": [entry(f"Subject {i}", aliases=["語" * 150]) for i in range(200)],
        "tags": [],
    }
    retained = []
    for index in range(200):
        with analysis.reuse_candidates(retained=retained):
            result = analysis.analysis_candidates(taxonomy, {"title": f"Subject {index}"})
        assert result["topics"] == [analysis._prompt_catalog_item(taxonomy["topics"][index])]
        prepared = retained[0]
        assert len(prepared.prompt_items) <= 8
        assert prepared.prompt_bytes <= 1024
        assert len(prepared.results) <= 8
    assert 0 not in retained[0].prompt_items
    oversized = analysis._PreparedCatalog(
        {"topics": [entry("Large", aliases=["x" * 2000])], "tags": []}
    )
    assert oversized.prompt_item(0)[0]["aliases"] == ["x" * 2000]
    assert not oversized.prompt_items and oversized.prompt_bytes == 0


def test_catalogue_equivalence_checks_do_not_repopulate_global_term_cache(monkeypatch):
    monkeypatch.setattr(
        analysis,
        "get_settings",
        lambda: configuration(
            analysis_max_candidates=2,
            analysis_fallback_candidates=1,
        ),
    )
    previous = {"topics": [entry("Zulu old fallback")], "tags": []}
    job = SimpleNamespace(
        catalog_hash=analysis.snapshot_hash(previous), catalog_snapshot=previous, result={}
    )
    taxonomy = {"topics": [entry("Alpha new fallback"), previous["topics"][0]], "tags": []}
    before = analysis.candidate_terms.cache_info()
    with analysis.reuse_candidates():
        assert analysis.analysis_catalog_current(job, taxonomy, {"title": "No matched identity"})
    assert analysis.candidate_terms.cache_info() == before


def test_replacing_prepared_generation_releases_old_indexes_before_new_allocation(monkeypatch):
    import weakref

    references = []
    original = analysis._PreparedCatalog

    class TrackedCatalog(original):
        def __init__(self, taxonomy):
            assert not references or references[-1]() is None
            super().__init__(taxonomy)
            references.append(weakref.ref(self))

    monkeypatch.setattr(analysis, "_PreparedCatalog", TrackedCatalog)
    retained = []
    for generation in range(3):
        with analysis.reuse_candidates(retained=retained):
            assert analysis.analysis_candidates(
                {"topics": [entry(f"Generation {generation}")], "tags": []},
                {"title": f"Generation {generation}"},
            )["topics"]
    assert len(references) == 3


@pytest.mark.parametrize("repeat", [1, 2, 1000])
def test_repeated_terminal_matches_preserve_weighted_fields_overlaps_and_suffixes(repeat):
    items = [
        entry("Go", aliases=["Go lang", "lang", "GO"], keywords=["Go lang", "lang"]),
        entry("C++", aliases=["C#", "工具", "ＡＩ", "foo_bar"]),
        entry("Suffix", aliases=["lang tools", "tools", "Go lang tools"]),
        entry("...", aliases=["---", ""]),
    ]
    phrase = "Go lang tools C++ C# 工具 AI foo_bar "
    snapshot = {field: phrase * repeat for field in ("title", "source_summary", "text")}
    root, frequency, postings = analysis._candidate_indexes(items)
    expected = [analysis.candidate_score(item, snapshot) for item in items]
    for _ in range(2):
        assert (
            analysis._candidate_scores(root, frequency, postings, len(items), snapshot) == expected
        )
    # Matching once in each weighted field must not suppress another field,
    # another position's longer/suffix phrase, or a subsequent independent call.
    assert expected == [
        analysis.candidate_score(item, {field: phrase for field in snapshot}) for item in items
    ]


def test_repeated_terminal_lists_are_consumed_once_per_text_and_released_between_calls():
    class MatchList(list):
        reads = 0

        def __iter__(self):
            self.reads += 1
            return super().__iter__()

    matches = MatchList([(0, 0, " python ")])
    root = {"python": {None: matches}}
    snapshot = {field: "Python " * 1000 for field in ("title", "source_summary", "text")}
    for iteration in range(2):
        assert analysis._candidate_scores(root, {}, {}, 1, snapshot) == [52]
        assert matches.reads == 3 * (iteration + 1)


def test_new_terminal_vocabulary_is_not_retained_between_independent_catalogs():
    # Keep a weak reference to a terminal list: the per-text optimization must
    # neither retain it across calls nor remember an earlier object's identity.
    import gc
    import weakref

    class MatchList(list):
        pass

    def evaluate():
        matches = MatchList([(0, 1, " python ")])
        reference = weakref.ref(matches)
        assert analysis._candidate_scores(
            {"python": {None: matches}}, {}, {}, 1, {"text": "Python " * 200}
        ) == [1]
        return reference

    references = [evaluate() for _ in range(100)]
    gc.collect()
    assert all(reference() is None for reference in references)
    assert analysis._candidate_scores(
        {"python": {None: [(0, 0, " python ")]}}, {}, {}, 1, {"title": "Python Python"}
    ) == [32]


@pytest.mark.parametrize("repeat", [1, 100])
def test_repeated_match_candidates_equal_the_unindexed_semantic_reference(monkeypatch, repeat):
    settings = configuration(analysis_max_candidates=3, analysis_fallback_candidates=1)
    monkeypatch.setattr(analysis, "get_settings", lambda: settings)
    oversized = entry("A oversized", aliases=["λ" * 140000], keywords=["Python"])
    taxonomy = {
        "topics": [
            oversized,
            entry("alpha", id=str(uuid.UUID(int=2)), aliases=["Go lang", "lang"]),
            entry("Alpha", id=str(uuid.UUID(int=1)), keywords=["Python", "Go lang"]),
            entry("工具", aliases=["C++", "foo_bar"]),
            entry("Unmatched fallback"),
        ],
        "tags": [entry("Tags", aliases=["Go lang", "C++"]), entry("Zero fallback")],
    }
    snapshot = {
        field: "Python Go lang C++ 工具 foo_bar " * repeat
        for field in ("title", "source_summary", "text")
    }
    entries = [item for field in ("topics", "tags") for item in taxonomy[field]]

    def semantic_reference(_root, _frequency, _postings, _size, value):
        # Independent substring-based semantics, not the trie traversal under test.
        return [analysis.candidate_score(item, value, cache_terms=False) for item in entries]

    with monkeypatch.context() as reference:
        reference.setattr(analysis, "_candidate_scores", semantic_reference)
        with analysis.reuse_candidates():
            expected = analysis.analysis_candidates(taxonomy, snapshot)
    with analysis.reuse_candidates():
        actual = analysis.analysis_candidates(taxonomy, snapshot)
    assert actual == expected
    assert oversized["id"] not in {item["id"] for item in actual["topics"]}
    assert len(actual["topics"]) == settings.analysis_max_candidates


@pytest.mark.parametrize("vocabulary", ["shared", "unique", "unicode", "large"])
def test_prepared_vocabulary_reuse_keeps_all_indexes_and_rankings_exact(vocabulary):
    taxonomy = {"topics": [], "tags": []}
    for index in range(160):
        item = entry(f"Runtime {index}", aliases=[f"Runtime alias {index}"])
        item["keywords"] = (
            ["container runtime", "Python", "distributed systems"]
            if vocabulary == "shared"
            else [f"keyword {index} {j}" for j in range(3)]
        )
        item["description"] = "Container runtime and Python systems."
        if vocabulary == "unicode":
            item["aliases"] += ["Ｃ＋＋", "Straße", "数学", "é", "e\u0301"]
        elif vocabulary == "large":
            item["keywords"] += ["x" * 20_000]
        taxonomy["topics" if index % 2 else "tags"].append(item)
    prepared = analysis._PreparedCatalog(taxonomy)
    entries = [item for _, item in prepared.entries]
    assert prepared.indexes == analysis._candidate_indexes(entries)
    for snapshot in ({"title": "Container runtime in Python"}, {"text": "数学 and STRASSE"}, {}):
        expected = analysis.candidate_scores(entries, snapshot)
        assert analysis._candidate_scores(*prepared.indexes, len(entries), snapshot) == expected


def test_prepared_normalization_reuses_only_bounded_shared_vocabulary(monkeypatch):
    taxonomy = {
        "topics": [
            entry(f"Runtime {i}", keywords=["shared", "python", "systems"]) for i in range(200)
        ],
        "tags": [],
    }
    calls = []
    original = analysis.candidate_text

    def normalize(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(analysis, "candidate_text", normalize)
    analysis._PreparedCatalog(taxonomy)
    assert calls.count("python") == calls.count("shared") == calls.count("systems") == 1
    calls.clear()
    # No vocabulary or prepared data survives index construction.
    analysis._PreparedCatalog(taxonomy)
    assert calls.count("python") == 1
    assert not analysis._shared_vocabulary([entry(f"Unique {i}") for i in range(32)])


def test_prepared_normalization_keeps_large_and_unicode_values_on_exact_path(monkeypatch):
    repeated = ["common", "word", "terms", "shared"]
    large = "x" * (analysis.CANDIDATE_NORMALIZATION_CACHE_CHARS + 1)
    unicode_value = "Straße"
    taxonomy = {
        "topics": [
            entry(f"Topic {i}", keywords=[*repeated, large, unicode_value]) for i in range(100)
        ],
        "tags": [],
    }
    calls = []
    original = analysis.candidate_text

    def normalize(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(analysis, "candidate_text", normalize)
    analysis._PreparedCatalog(taxonomy)
    assert calls.count("common") == 1
    assert calls.count(large) == calls.count(unicode_value) == 100
