import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from devfeed_aggregator import article_pages, article_tasks, language_backfill
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.models import Article

NOW = datetime(2026, 9, 6, tzinfo=UTC)
PROSE = (
    "This article explains how developers can build reliable applications with Python and store "
    "their information in a database. We discuss common problems and test all changes before "
    "making the application available to readers. The following examples show how the system "
    "works. "
)
JAPANESE = (
    "この記事では開発者向けのニュースを収集し、記事の言語を自動的に判定する方法について詳しく説明します。"
    "データベースに正しい情報を保存することで、読みたい言語の記事を簡単に探せるようになります。"
    "実際の例を使いながら、新しい機能を公開する前に確認すべき点も紹介します。"
)


def html_page(*, text=PROSE * 4, metadata="", title="Original article"):
    html = f"""<html lang="fr"><head><title>{title}</title>{metadata}</head><body>
    <nav>Log in Home About Contact</nav><article><h1>{title}</h1><p>{text}</p></article>
    <footer>Subscribe for more updates</footer></body></html>"""
    return FetchResult(
        200,
        html.encode(),
        "https://unfamiliar-publisher.example/posts/article",
        content_type="text/html",
    )


def test_page_extraction_uses_generic_content_and_never_site_language():
    result = article_pages.extract_article(
        html_page(
            metadata="""
        <meta name="author" content="Actual Writer">
        <meta property="article:published_time" content="2026-09-05T10:00:00+05:30">
        <meta property="og:image" content="/cover.png">
        <link rel="canonical" href="https://different.example/identity">
    """
        ),
        NOW,
    )
    assert result.title == "Original article" and result.author == "Actual Writer"
    assert result.published_at == datetime(2026, 9, 5, 4, 30, tzinfo=UTC)
    assert result.image_url == "https://unfamiliar-publisher.example/cover.png"
    assert result.language == "en" and result.evidence["declared_language"] == "fr"
    assert result.text_source == "body"
    assert 0 < len(result.summary) <= 500
    assert "Subscribe" not in result.summary and "Log in" not in result.summary
    assert "canonical_url" not in result.evidence and "body" not in result.evidence
    assert not result.summary.startswith("---")  # No extractor metadata front matter.


def test_body_language_wins_over_translated_preview_and_english_code():
    result = article_pages.extract_article(
        html_page(
            text=JAPANESE * 4 + f"<pre><code>{PROSE * 30}</code></pre>",
            metadata=f'<meta name="description" content="{PROSE}">',
        ),
        NOW,
    )
    assert result.language == "ja" and result.text_source == "body"
    assert "This article" in result.summary  # Public metadata can be translated.


@pytest.mark.parametrize(
    "metadata",
    [
        '<meta property="article:tag" content="Kubernetes">'
        '<meta property="article:tag" content="Security">',
        '<meta name="keywords" content="Kubernetes, Security, kubernetes">',
        '<meta name="news_keywords" content="Kubernetes, Security">',
        '<script type="application/ld+json">'
        '{"@type":"BlogPosting","keywords":"Kubernetes, Security"}</script>',
        '<script type="application/ld+json">'
        '{"@graph":[{"@type":"Organization","keywords":"Not an article tag"},'
        '{"@type":"TechArticle","keywords":["Kubernetes",null,{}],'
        '"articleSection":"Security"}]}</script>',
    ],
)
def test_page_imports_explicit_metadata_tags(metadata):
    page = article_pages.extract_article(html_page(metadata=metadata), NOW)
    assert page.tags == ["Kubernetes", "Security"]
    assert page.evidence["tags"] == page.tags


def test_page_imports_article_category_links_without_navigation_or_related_article_tags():
    body = html_page().body.replace(
        b"</article>",
        b"""<aside><ul>
        <li><a href="/posts/category/kubernetes-v1-33"><span>Kubernetes v1.33</span></a></li>
        <li><a href="/posts/category/security">Security</a></li>
        <li><a href="/posts/category/kubernetes">Kubernetes</a></li>
        <li><a href="https://another.example/category/unrelated">External</a></li>
        </ul></aside>
        <p><a href="/category/citation">Category mentioned in prose</a></p>
        <a rel="tag" href="/labels/c-plus-plus">C++</a>
        <nav><a rel="tag" href="/tags/menu">Menu</a></nav>
        <div hidden><a rel="tag" href="/tags/hidden">Hidden</a></div>
        <article><ul><li><a rel="tag" href="/tags/related">Related post</a></li></ul></article>
        </article><aside><ul><li><a href="/category/global">Global category</a></li></ul></aside>
        <footer><a rel="tag" href="/tags/site">Site footer</a></footer>""",
    )
    page = article_pages.extract_article(replace(html_page(), body=body), NOW)
    assert page.tags == ["Kubernetes v1.33", "Security", "Kubernetes", "C++"]


def test_page_tag_labels_are_bounded_and_multiple_articles_do_not_supply_category_links():
    metadata = '<meta name="keywords" content="' + ",".join(f"Tag {n}" for n in range(40)) + '">'
    page = article_pages.extract_article(html_page(metadata=metadata), NOW)
    assert len(page.tags) == 30
    listing = html_page().body.replace(
        b"</article>",
        b"""<ul><li><a href="/category/one">One</a></li></ul></article>
        <article><h1>Another article</h1><ul>
        <li><a rel="tag" href="/tags/two">Two</a></li></ul></article>""",
    )
    assert article_pages.extract_article(replace(html_page(), body=listing), NOW).tags == []


def test_streamed_article_tags_survive_transport_wrappers_but_hidden_content_stays_ignored():
    body = b"""<html><head><title>Original article</title></head><body>
    <div hidden id="S:2"><article><h1>Original article</h1><aside><ul>
    <li><a href="/posts/category/kubernetes">Kubernetes</a></li>
    <li hidden><a href="/posts/category/hidden">Hidden</a></li>
    </ul></aside></article></div>
    <article><a rel="tag" href="/tags/other">Other article</a></article>
    <script>$RS("S:2","P:2")</script></body></html>"""
    page = article_pages.extract_article(replace(html_page(), body=body), NOW)
    assert page.tags == ["Kubernetes"]
    incomplete = body.replace(b'$RS("S:2","P:2")', b"")
    assert article_pages.extract_article(replace(html_page(), body=incomplete), NOW).tags == []


def test_page_language_backfill_keeps_main_body_result(monkeypatch):
    from contextlib import contextmanager

    row = SimpleNamespace(
        id=uuid.uuid4(),
        title="English headline",
        summary=PROSE,
        metadata_source_type="page",
        language="ja",
    )

    @contextmanager
    def factory():
        yield SimpleNamespace(execute=lambda _: SimpleNamespace(all=lambda: [row]))

    monkeypatch.setattr(language_backfill, "session_factory", lambda: factory)
    monkeypatch.setattr(
        language_backfill, "detect_language", lambda *_: pytest.fail("Used excerpt")
    )
    result = language_backfill.backfill_languages()
    assert result["updated"] == 0 and result["items"][0]["language"] == "ja"
    assert result["items"][0]["reason"] == "page_body_already_detected"


@pytest.mark.parametrize(
    "title", ["Just a moment...", "Access Denied", "Sign in", "Making sure you're not a bot!"]
)
def test_soft_error_and_challenge_pages_are_not_article_content(title):
    with pytest.raises(FeedError, match="login, error or browser challenge") as error:
        article_pages.extract_article(html_page(title=title), NOW)
    assert error.value.reason == "page_unavailable" and not error.value.retryable


@pytest.mark.parametrize(
    "date", ["2026-09-05", "2026-09-05T10:00:00", "2099-01-01T00:00:00Z", "invalid"]
)
def test_no_guessed_timezone_or_future_publication_date(date):
    result = article_pages.extract_article(
        html_page(
            metadata=f'''
        <meta property="article:published_time" content="{date}">
        <meta property="article:modified_time" content="2026-09-05T12:00:00Z">
    '''
        ),
        NOW,
    )
    assert result.published_at is None


def test_json_ld_published_date_not_related_organization_date():
    document = {
        "@graph": [
            {"@type": "Organization", "datePublished": "2000-01-01T00:00:00Z"},
            {
                "@type": "BlogPosting",
                "headline": "Original article",
                "datePublished": "2026-09-04T12:00:00Z",
                "author": {"@type": "Person", "name": "Original Writer"},
            },
        ]
    }
    result = article_pages.extract_article(
        html_page(metadata=f'<script type="application/ld+json">{json.dumps(document)}</script>'),
        NOW,
    )
    assert result.author == "Original Writer"
    assert result.published_at == datetime(2026, 9, 4, 12, tzinfo=UTC)


def test_paywall_uses_only_public_description_not_hidden_full_text():
    metadata = f'''<meta name="description" content="{PROSE}">
        <script type="application/ld+json">
        {{"@type":"Article","isAccessibleForFree":false}}</script>'''
    result = article_pages.extract_article(html_page(text=JAPANESE * 4, metadata=metadata), NOW)
    assert result.text_source == "description" and result.language == "en"
    assert result.evidence["paywalled"] is True


def test_missing_text_only_discovers_explicit_image_not_a_random_logo():
    body = (
        b'<html><head><title>Empty page</title><meta property="og:image" '
        b'content="/preview.png"></head><body><img src="/logo.png"></body></html>'
    )
    result = article_pages.extract_article(FetchResult(200, body, "https://example.com/empty"), NOW)
    assert not result.has_text and result.language is None and result.summary == ""
    assert result.image_url == "https://example.com/preview.png"


def test_private_preview_url_is_not_promoted():
    result = article_pages.extract_article(
        html_page(metadata='<meta property="og:image" content="http://127.0.0.1/secret">'), NOW
    )
    assert result.image_url is None


def test_excerpt_and_malformed_json_are_bounded():
    assert article_pages.excerpt("短い文章です。") == "短い文章です。"
    assert len(article_pages.excerpt("これは長い文章です。" * 500)) <= 500
    assert list(article_pages.article_nodes(["{bad json", "[" * 2000])) == []


def blank_article(**values):
    return Article(
        id=uuid.uuid4(),
        title="English submission title",
        summary="",
        author=None,
        published_at=None,
        language=None,
        image_url=values.pop("image_url", None),
        metadata_source_type=values.pop("metadata_source_type", "aggregator"),
        canonical_url="https://example.com/original",
        url_hash="hash",
        feed_at=NOW,
        discovered_at=NOW,
        content_type="article",
        **values,
    )


def test_page_merge_preserves_identity_order_and_existing_image():
    page = article_pages.extract_article(
        html_page(metadata='<meta property="og:image" content="/cover.png">'), NOW
    )
    article = blank_article(image_url="https://example.com/keep.png")
    session = SimpleNamespace(scalars=lambda _: SimpleNamespace(all=lambda: []))
    changed = article_tasks.apply_page(session, article, page)
    assert article.metadata_source_type == "page" and article.language == "en"
    assert article.title == page.title and article.summary == page.summary
    assert article.image_url == "https://example.com/keep.png"
    assert article.canonical_url == "https://example.com/original" and article.url_hash == "hash"
    assert article.feed_at == article.discovered_at == NOW
    assert "image_url" not in changed
    assert article_tasks.apply_page(session, article, page) == []


def test_publisher_metadata_wins_even_when_page_finishes_later():
    page = article_pages.extract_article(html_page(), NOW)
    article = blank_article(metadata_source_type="publisher")
    session = SimpleNamespace(scalars=lambda _: SimpleNamespace(all=lambda: []))
    changed = article_tasks.apply_page(session, article, page)
    assert article.title == "English submission title"
    assert article.summary == page.summary and article.language == "en"
    assert article.metadata_source_type == "publisher"
    assert "title" not in changed and "summary" in changed
    article.summary = "Publisher supplied summary"
    assert article_tasks.apply_page(session, article, page) == []
    assert article.summary == "Publisher supplied summary"


def test_missing_page_fields_do_not_erase_previous_metadata():
    page = article_pages.extract_article(html_page(), NOW)
    article = blank_article(metadata_source_type="page")
    article.author, article.published_at, article.language = "Known Author", NOW, "ja"
    session = SimpleNamespace(scalars=lambda _: SimpleNamespace(all=lambda: []))
    article_tasks.apply_page(
        session, article, replace(page, author=None, published_at=None, language=None)
    )
    assert (article.author, article.published_at, article.language) == ("Known Author", NOW, "ja")
