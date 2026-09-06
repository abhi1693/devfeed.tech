import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from devfeed_aggregator import language_backfill, languages, tasks
from devfeed_cli.main import run
from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.feeds.parser import parse_feed
from devfeed_core.log_text import event_text
from devfeed_core.models import IngestionJob, Source
from sqlalchemy.dialects import postgresql

ENGLISH = (
    "This article explains how developers can build reliable applications and store their data "
    "in a database. We will look at practical examples, discuss common problems, and show how "
    "to test the changes before releasing them to readers."
)
JAPANESE = (
    "この記事では開発者向けのニュースを収集し、記事の言語を自動的に判定する方法について詳しく説明します。"
    "データベースに正しい情報を保存することで、読みたい言語の記事を簡単に探せるようになります。"
    "実際の例を使いながら、新しい機能を公開する前に確認すべき点も紹介します。"
)
NOW = datetime(2026, 9, 6, tzinfo=UTC)


@pytest.mark.parametrize(
    "expected,text",
    [
        ("en", ENGLISH),
        ("ja", JAPANESE),
        (
            "fr",
            "Cet article explique comment créer des applications fiables et conserver les "
            "informations dans une base de données. Nous allons examiner plusieurs exemples "
            "pratiques et vérifier les modifications avant de les publier pour nos lecteurs.",
        ),
        (
            "es",
            "Este artículo explica cómo crear aplicaciones fiables y guardar la información "
            "en una base de datos. Vamos a estudiar varios ejemplos prácticos y comprobar los "
            "cambios antes de publicarlos para nuestros lectores.",
        ),
        (
            "de",
            "Dieser Artikel erklärt, wie Entwickler zuverlässige Anwendungen erstellen und "
            "Informationen in einer Datenbank speichern können. Wir untersuchen praktische "
            "Beispiele und überprüfen die Änderungen vor der Veröffentlichung.",
        ),
        (
            "pt",
            "Este artigo explica como desenvolver aplicações confiáveis e armazenar as "
            "informações em um banco de dados. Vamos analisar exemplos práticos e verificar as "
            "alterações antes de publicá-las para os nossos leitores.",
        ),
        (
            "ru",
            "В этой статье объясняется, как разработчики могут создавать надёжные приложения "
            "и хранить информацию в базе данных. Мы рассмотрим практические примеры и проверим "
            "изменения перед публикацией для наших читателей.",
        ),
        (
            "zh",
            "本文介绍开发者如何构建可靠的应用程序，并将信息保存在数据库中。我们将分析实际示例，"
            "讨论常见问题，并在向读者发布新功能之前检查所有修改。自动识别文章的语言可以帮助读者找到"
            "适合自己的内容，同时避免把不同语言的文章错误地归类到同一种语言中。",
        ),
        (
            "ko",
            "이 글에서는 개발자가 안정적인 애플리케이션을 만들고 데이터베이스에 정보를 저장하는 "
            "방법을 설명합니다. 실제 사례를 살펴보고 일반적인 문제를 논의한 다음 독자에게 새로운 "
            "기능을 공개하기 전에 변경 사항을 확인하겠습니다.",
        ),
        (
            "hi",
            "इस लेख में हम समझाते हैं कि विकासकर्ता विश्वसनीय अनुप्रयोग कैसे बना सकते हैं और "
            "जानकारी को डेटाबेस में कैसे रख सकते हैं। हम व्यावहारिक उदाहरणों को देखेंगे और पाठकों के "
            "लिए प्रकाशित करने से पहले सभी परिवर्तनों की जाँच करेंगे।",
        ),
    ],
)
def test_offline_detection_uses_prose_not_english_headline(expected, text):
    result = languages.detect_language(
        "A guide for developers using Python and Docker", text, "publisher"
    )
    assert result.language == expected
    assert result.reason == "detected"
    assert result.confidence >= languages.MIN_CONFIDENCE


@pytest.mark.parametrize("source_type", ["aggregator", None])
def test_discovery_title_and_submission_text_cannot_determine_article_language(
    monkeypatch, source_type
):
    monkeypatch.setattr(
        languages, "_detector", lambda *_: pytest.fail("Loaded model for discovery text")
    )
    assert (
        languages.detect_language(ENGLISH, ENGLISH, source_type).reason == "publisher_text_required"
    )


@pytest.mark.parametrize(
    "text", ["", "Python", "Rust SIMD on the GPU", "123456 !@# 🦀", "prologue"]
)
def test_short_and_nonlinguistic_inputs_abstain_without_loading_models(monkeypatch, text):
    monkeypatch.setattr(
        languages, "_detector", lambda *_: pytest.fail("Loaded model for tiny input")
    )
    result = languages.detect_language(text, "", "publisher")
    assert result.language is None and result.reason == "insufficient_text"


def test_technical_terms_do_not_force_a_language():
    result = languages.detect_language(
        "Python API",
        "API SDK HTTP JSON REST CRUD SQL CSS HTML XML YAML TOML HTTP API SDK",
        "publisher",
    )
    assert result.language is None and result.reason == "uncertain"


@pytest.mark.parametrize("scores", [[], [0.79, 0.01], [0.81, 0.70]])
def test_confidence_and_margin_gates(monkeypatch, scores):
    detector = SimpleNamespace(
        compute_language_confidence_values=lambda _: [
            SimpleNamespace(value=score) for score in scores
        ]
    )
    monkeypatch.setattr(languages, "_detector", lambda *_: detector)
    result = languages.detect_language("Example", ENGLISH, "publisher")
    assert result.language is None and result.reason == "uncertain"


def test_prose_removes_code_urls_and_invisible_markup():
    text = (
        "<p>Hello <b>reader</b></p><script>hidden script</script><style>hidden style</style>"
        "<pre><code>hidden code</code></pre> ```python\nhidden fence\n``` `hidden inline` "
        "[Documentation](https://example.com/english-language-guide) https://example.com/another "
        "reader@example.com"
    )
    assert languages.prose(text) == "Hello reader Documentation"
    assert languages.prose("~~~python\nunclosed code") == ""
    assert len(languages.prose("a " * 100_000)) <= languages.MAX_TEXT


def japanese_feed():
    return (
        '<rss version="2.0"><channel><title>International feed</title><language>en-US</language>'
        "<item><guid>ja-1</guid><link>https://example.com/japanese</link>"
        f"<title>DESIGN.mdとは？AIと進めるUIデザインの共有ガイド</title><description>{JAPANESE}</description>"
        "</item></channel></rss>"
    ).encode()


def test_preflight_never_loads_detector_and_feed_evidence_remains_unchanged(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(languages, "_detector", lambda *_: pytest.fail("Inference during parsing"))
        parsed = parse_feed(
            japanese_feed(), "https://example.com/rss", NOW, source_type="publisher"
        )
    assert parsed.entries[0].language is None
    enriched = languages.detect_feed_languages(parsed)
    assert enriched.entries[0].language == "ja"
    assert parsed.entries[0].language is None
    assert enriched.entries[0].source_metadata == parsed.entries[0].source_metadata
    assert enriched.entries[0].source_metadata["language"] == "en-us"
    assert enriched.profile == parsed.profile and enriched.profile.language == "en-us"


@pytest.mark.parametrize("status", [200, 304])
def test_worker_detects_outside_transaction_only_for_new_response(monkeypatch, status):
    source = Source(
        id=uuid.uuid4(),
        source_type="publisher",
        approval_status="approved",
        enabled=True,
        feed_url="https://example.com/rss",
        poll_interval_seconds=1800,
    )
    job = IngestionJob(
        id=uuid.uuid4(),
        source_id=source.id,
        status="running",
        attempts=1,
        lease_token=uuid.uuid4(),
        entries_seen=0,
        entries_skipped=0,
        articles_created=0,
    )
    active = False
    calls = []

    @contextmanager
    def begin():
        nonlocal active
        active = True
        values = iter([job, source])
        try:
            yield SimpleNamespace(scalar=lambda _: next(values))
        finally:
            active = False

    original = languages.detect_feed_languages

    def detect(parsed):
        assert not active
        calls.append("detect")
        return original(parsed)

    def store(session, source_id, parsed):
        assert active and parsed.entries[0].language == "ja"
        calls.append("store")
        return 1

    monkeypatch.setattr(tasks, "session_factory", lambda: SimpleNamespace(begin=begin))
    monkeypatch.setattr(tasks, "claim_job", lambda *_: (job, source))
    monkeypatch.setattr(
        tasks, "fetch_feed", lambda *_: FetchResult(status, japanese_feed(), source.feed_url)
    )
    monkeypatch.setattr(tasks, "detect_feed_languages", detect)
    monkeypatch.setattr(tasks, "store_entries", store)
    tasks.ingest(str(job.id))
    assert job.status == "succeeded"
    assert calls == (["detect", "store"] if status == 200 else [])


def row(number=1, *, summary=JAPANESE, language="en", source_type="publisher"):
    return SimpleNamespace(
        id=uuid.UUID(int=number),
        title="Article title",
        summary=summary,
        metadata_source_type=source_type,
        language=language,
    )


class Factory:
    def __init__(self, rows, *, skip_write=False):
        self.rows = rows
        self.active = False
        self.writes = []
        self.reads = []
        self.transactions = 0
        self.skip_write = skip_write

    @contextmanager
    def __call__(self):
        self.active = True
        try:
            yield self
        finally:
            self.active = False

    @contextmanager
    def begin(self):
        self.transactions += 1
        with self() as session:
            yield session

    def execute(self, stmt):
        self.reads.append(stmt.compile(dialect=postgresql.dialect()))
        return SimpleNamespace(all=lambda: self.rows)

    def scalar(self, stmt):
        self.writes.append(stmt.compile(dialect=postgresql.dialect()))
        return None if self.skip_write else self.rows[0].id


@pytest.mark.parametrize("dry_run", [False, True])
def test_backfill_changes_existing_incorrect_hints_without_holding_db_during_inference(
    monkeypatch, dry_run
):
    factory = Factory([row()])
    original = language_backfill.detect_language

    def detect(*args):
        assert not factory.active
        return original(*args)

    monkeypatch.setattr(language_backfill, "session_factory", lambda: factory)
    monkeypatch.setattr(language_backfill, "detect_language", detect)
    result = language_backfill.backfill_languages(dry_run=dry_run)
    assert result["scanned"] == result["detected"] == result["would_change"] == 1
    assert result["updated"] == (0 if dry_run else 1)
    assert result["items"][0]["language"] == "ja"
    assert factory.transactions == (0 if dry_run else 1)
    if not dry_run:
        sql = str(factory.writes[0])
        assert "SET language=" in sql and "RETURNING articles.id" in sql
        assert all(
            f"articles.{field}" in sql
            for field in ["title", "summary", "metadata_source_type", "language"]
        )
        assert "IS NOT DISTINCT FROM" in sql
        assert factory.writes[0].params["language"] == "ja"


@pytest.mark.parametrize(
    "rows", [[], [row(language="ja")], [row(summary="", language=None, source_type="aggregator")]]
)
def test_empty_and_unchanged_backfills_do_not_issue_updates_or_invalidate_cache(monkeypatch, rows):
    factory = Factory(rows)
    monkeypatch.setattr(language_backfill, "session_factory", lambda: factory)
    result = language_backfill.backfill_languages()
    assert result["updated"] == result["would_change"] == 0
    assert factory.transactions == 0 and factory.writes == []


def test_backfill_skips_concurrent_change_or_deletion(monkeypatch):
    factory = Factory([row()], skip_write=True)
    monkeypatch.setattr(language_backfill, "session_factory", lambda: factory)
    result = language_backfill.backfill_languages()
    assert result["would_change"] == result["concurrent_changes_skipped"] == 1
    assert result["updated"] == 0 and result["items"][0]["updated"] is False


def test_backfill_clears_unsubstantiated_legacy_hints(monkeypatch):
    factory = Factory([row(summary="", source_type="aggregator")])
    monkeypatch.setattr(language_backfill, "session_factory", lambda: factory)
    result = language_backfill.backfill_languages()
    assert result["unknown"] == result["updated"] == 1
    assert result["items"][0]["language"] is None


def test_backfill_source_filter_and_keyset_resume_are_bounded(monkeypatch):
    factory = Factory([row(2), row(3)])
    monkeypatch.setattr(language_backfill, "session_factory", lambda: factory)
    source_id, after = uuid.uuid4(), uuid.UUID(int=1)
    result = language_backfill.backfill_languages(
        limit=1, after=after, source_id=source_id, dry_run=True
    )
    assert result["scanned"] == 1 and result["next_after"] == str(uuid.UUID(int=2))
    statement = factory.reads[0]
    assert "EXISTS" in str(statement) and "articles.id >" in str(statement)
    assert source_id in statement.params.values() and after in statement.params.values()
    assert statement.params["param_1"] == 2


@pytest.mark.parametrize("limit", [0, 501])
def test_invalid_backfill_limit_rejected_before_database_access(limit, monkeypatch):
    monkeypatch.setattr(
        language_backfill, "session_factory", lambda: pytest.fail("Opened database")
    )
    with pytest.raises(ValueError):
        language_backfill.backfill_languages(limit=limit)


def test_cli_wires_preview_and_pagination(monkeypatch, capsys):
    from devfeed_cli import articles

    captured = []
    monkeypatch.setattr(
        articles, "backfill_languages", lambda **kwargs: captured.append(kwargs) or {"scanned": 0}
    )
    after, source_id = uuid.uuid4(), uuid.uuid4()
    assert (
        run(
            [
                "articles",
                "detect-languages",
                "--dry-run",
                "--limit",
                "5",
                "--after",
                str(after),
                "--source-id",
                str(source_id),
            ]
        )
        == 0
    )
    assert captured == [{"limit": 5, "after": after, "source_id": source_id, "dry_run": True}]
    assert json.loads(capsys.readouterr().out) == {"scanned": 0}


@pytest.mark.parametrize(
    "event,preview,ending",
    [
        ("article_languages_detected", False, "unknown"),
        ("article_languages_backfilled", False, "; 1 updated"),
        ("article_languages_backfilled", True, "(preview; no changes)"),
    ],
)
def test_language_logs_are_human_readable(event, preview, ending):
    text = event_text(
        {
            "event": event,
            "languages_detected": 5,
            "languages_unknown": 2,
            "articles_updated": 1,
            "dry_run": preview,
        }
    )
    assert text.startswith("Article languages: 5 detected, 2 unknown") and text.endswith(ending)
