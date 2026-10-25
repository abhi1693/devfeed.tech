"""Resume pending articles and give every completed attempt an editorial outcome."""

import uuid
from datetime import timedelta
from itertools import zip_longest
from time import monotonic

from sqlalchemy import func, select, true
from sqlalchemy.orm import defer, lazyload

from devfeed_core.ai_content import eligible_article, eligible_articles
from devfeed_core.analysis import (
    PROMPT_VERSION,
    VALIDATION_VERSION,
    analysis_catalog_current,
    candidate_evidence,
    candidate_score,
    catalog,
    request_analysis,
    reuse_candidates,
    snapshot_hash,
    source_snapshot,
)
from devfeed_core.article_jobs import (
    EXTRACTION_VERSION,
    approved_sources,
    request_article_enrichment,
)
from devfeed_core.article_lifecycle import reconcile_processing
from devfeed_core.automation_demands import (
    expand_automation_demands,
    maintenance_lane,
    observe_automation_demand,
    wait_for_dependency,
    wait_for_job,
)
from devfeed_core.catalog_cache import reuse_snapshots, snapshot_current
from devfeed_core.catalog_cache import snapshot as catalog_snapshot
from devfeed_core.config import get_settings
from devfeed_core.editorial import EditorialDecision, decide_article, meaningful_text
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleContent,
    ArticleEnrichmentJob,
    ArticleOrigin,
    ArticleTag,
    Source,
    Tag,
    Topic,
    TopicProposal,
    utcnow,
)
from devfeed_core.publication_policy import apply_publication_policy, evaluate_publication
from devfeed_core.tag_topic_discovery import record_keys
from devfeed_core.topics import TopicWrite, lock_topics

CHECK_INTERVAL = timedelta(minutes=5)
TOPIC_WAIT = timedelta(hours=24)
SOURCE_ACTOR = "devfeed:source-automation"
_single_candidate_foreground = True


def _bounded_due_candidates(session, statement, batch, *, interleave=False):
    """Reserve both lanes without exceeding the expensive evaluation budget.

    Separate indexed queries avoid walking an old maintenance prefix to locate
    fresh work. Unused reservations return to the other lane. A one-item budget
    alternates its first choice; durable due work survives process restart.
    """
    global _single_candidate_foreground
    first_lane = 0 if _single_candidate_foreground else 1
    reservations = [(first_lane, 1)] if batch == 1 else [(0, (batch + 1) // 2), (1, batch // 2)]
    if batch == 1 or interleave:
        _single_candidate_foreground = not _single_candidate_foreground
    selected, identifiers = [], set()
    pages: dict[int, list] = {0: [], 1: []}

    def append(rows):
        added = []
        for row in rows:
            if row[0] not in identifiers:
                selected.append(row)
                identifiers.add(row[0])
                added.append(row)
        return added

    for lane, limit in reservations:
        scoped = statement.where(maintenance_lane() == lane)
        if identifiers:
            scoped = scoped.where(Article.id.not_in(identifiers))
        pages[lane] = append(session.execute(scoped.limit(limit)).all())
    if interleave:
        # A deadline may admit only one observation. Alternate the first lane
        # across cycles as well as interleaving this cycle's reserved rows.
        selected = [
            row
            for pair in zip_longest(pages[first_lane], pages[1 - first_lane])
            for row in pair
            if row is not None
        ]
    if len(selected) < batch:
        scoped = statement.where(Article.id.not_in(identifiers)) if identifiers else statement
        append(session.execute(scoped.limit(batch - len(selected))).all())
    return selected


def lock_article_catalog(session):
    # Serialize only proposal creation; ordinary classification applications use
    # a shared catalog lock. Acquire this before any catalog lock to avoid upgrades.
    # DFR namespace, source proposals; lane 0 is topic decision admission.
    session.execute(select(func.pg_advisory_xact_lock(0x444652, 1)))
    lock_topics(session, read=True)


def pending_topic_matches(session, snapshot) -> bool:
    # Matching consumes identities only. Avoid transferring full descriptions,
    # facts and evidence for every pending draft while holding publication locks.
    proposed = TopicProposal.proposed
    statement = select(
        func.jsonb_build_object(
            "name",
            proposed["name"],
            "slug",
            proposed["slug"],
            "aliases",
            func.coalesce(proposed["aliases"], func.jsonb_build_array()),
            "keywords",
            func.coalesce(proposed["keywords"], func.jsonb_build_array()),
        )
    ).where(TopicProposal.status == "pending")
    candidates = catalog_snapshot(
        session, ("topic_proposals",), lambda: list(session.scalars(statement))
    )
    evidence = candidate_evidence(snapshot)
    return any(candidate_score(draft, snapshot, evidence=evidence) > 0 for draft in candidates)


def propose_source_topics(session, article) -> int:
    """Serialize only actual proposal candidates; never approve identities."""
    if (
        not get_settings().full_automation
        or not get_settings().article_topic_proposals_enabled
        or not eligible_article(article)
    ):
        return 0
    statement = (
        select(Tag)
        .join(ArticleTag)
        .where(
            ArticleTag.article_id == article.id,
            ArticleTag.origin == "source",
            Tag.auto_link_topic.is_(True),
            Tag.topic_id.is_(None),
            ~select(TopicProposal.id).where(TopicProposal.slug == Tag.slug).exists(),
        )
        .order_by(Tag.id)
        .limit(get_settings().automation_batch_size)
    )
    if session.scalar(select(statement.exists())) is not True:
        return 0
    lock_article_catalog(session)
    tags = session.scalars(statement).all()
    if not tags:
        return 0
    slugs = [tag.slug for tag in tags]
    attempted = set(
        session.scalars(select(TopicProposal.slug).where(TopicProposal.slug.in_(slugs)))
    )
    keys = {tag.id: set(record_keys(tag)) for tag in tags}
    # Inactive catalog identities and previous rejections must not be recreated.
    existing = set()
    for terms in session.scalars(
        select(Topic.identity_keys).where(
            Topic.identity_keys.overlap(sorted(set().union(*keys.values())))
        )
    ):
        existing.update(terms)
    count = 0
    for tag in tags:
        if tag.slug in attempted or keys[tag.id] & existing:
            continue
        draft = TopicWrite(name=tag.name, slug=tag.slug, kind="unclassified")
        session.add(
            TopicProposal(
                batch_id=uuid.uuid4(),
                slug=tag.slug,
                action="create",
                origin="article_enrichment",
                source_name="Source tag discovery",
                proposed=draft.model_dump(mode="json"),
                evidence=[
                    {
                        "kind": "source_tag",
                        "tag_id": str(tag.id),
                        "article_id": str(article.id),
                        "url": article.canonical_url,
                    }
                ],
                created_by={
                    "subject": SOURCE_ACTOR,
                    "issuer": "devfeed",
                    "name": "Full automation",
                },
                research_requested=True,
            )
        )
        attempted.add(tag.slug)
        count += 1
    session.flush()
    return count


def schedule_source_admission(factory) -> int:
    from devfeed_core.source_relevance import schedule_pending_reviews

    return schedule_pending_reviews(factory, get_settings().automation_batch_size)


def reject_known_paywalled_articles(factory, *, now=None) -> int:
    """Apply the explicit paywall policy to pending records enriched before this policy."""
    latest_enrichment_id = (
        select(ArticleEnrichmentJob.id)
        .where(ArticleEnrichmentJob.article_id == Article.id)
        .order_by(ArticleEnrichmentJob.created_at.desc(), ArticleEnrichmentJob.id.desc())
        .limit(1)
        .correlate(Article)
        .scalar_subquery()
    )
    has_latest_paywall = (
        select(ArticleEnrichmentJob.id)
        .where(
            ArticleEnrichmentJob.id == latest_enrichment_id,
            ArticleEnrichmentJob.status == "succeeded",
            ArticleEnrichmentJob.result["paywalled"].astext == "true",
        )
        .exists()
    )
    now = now or utcnow()
    with factory() as session:
        if get_settings().automation_dependency_wakes_enabled:
            candidates = (
                select(Article.id)
                .where(
                    Article.review_status == "pending",
                    Article.publication_status == "unpublished",
                    Article.automation_next_check_at <= now,
                )
                .order_by(Article.automation_next_check_at, Article.id)
                .limit(get_settings().automation_batch_size)
                .cte("paywall_candidates")
                .prefix_with("MATERIALIZED")
            )
            latest = (
                select(
                    ArticleEnrichmentJob.status,
                    ArticleEnrichmentJob.result["paywalled"].astext.label("paywalled"),
                )
                .where(ArticleEnrichmentJob.article_id == candidates.c.id)
                .order_by(ArticleEnrichmentJob.created_at.desc(), ArticleEnrichmentJob.id.desc())
                .limit(1)
                .lateral()
            )
            statement = (
                select(candidates.c.id)
                .select_from(candidates)
                .join(latest, true())
                .where(latest.c.status == "succeeded", latest.c.paywalled == "true")
            )
        else:
            statement = (
                select(Article.id)
                .where(
                    Article.review_status == "pending",
                    Article.publication_status == "unpublished",
                    has_latest_paywall,
                )
                .order_by(Article.discovered_at, Article.id)
                .limit(get_settings().automation_batch_size)
            )
        identifiers = session.scalars(statement).all()

    rejected = 0
    for identifier in identifiers:
        with factory.begin() as session:
            # Preserve the established source -> article lock order.
            approved_sources(session, identifier, lock=True)
            article = session.scalar(
                select(Article).where(Article.id == identifier).with_for_update(of=Article)
            )
            if (
                article is None
                or article.review_status != "pending"
                or article.publication_status != "unpublished"
            ):
                continue
            decision = apply_publication_policy(session, article, None)
            rejected += decision["status"] == "rejected"
    return rejected


def schedule_article_automation(factory) -> dict[str, int]:
    with reuse_snapshots(), reuse_candidates():
        return _schedule_article_automation(factory)


def _prepare_article_candidates(factory, identifier):
    from devfeed_core.analysis import analysis_candidates

    with factory() as session:
        article = session.scalar(
            select(Article).where(Article.id == identifier).options(lazyload("*"))
        )
        if article is None:
            return None
        analysis_status = session.scalar(
            select(ArticleAnalysisJob.status)
            .where(ArticleAnalysisJob.article_id == identifier)
            .order_by(ArticleAnalysisJob.created_at.desc(), ArticleAnalysisJob.id.desc())
            .limit(1)
        )
        enrichment_status = session.scalar(
            select(ArticleEnrichmentJob.status)
            .where(ArticleEnrichmentJob.article_id == identifier)
            .order_by(ArticleEnrichmentJob.created_at.desc(), ArticleEnrichmentJob.id.desc())
            .limit(1)
        )
        if (
            analysis_status in {"queued", "running"}
            or enrichment_status in {"queued", "running"}
            or (analysis_status is None and enrichment_status is None)
        ):
            return None
        snapshot = source_snapshot(article, session.get(ArticleContent, identifier))
        taxonomy = catalog(session)
    # No row/catalog locks or open transaction during full-catalog ranking.
    analysis_candidates(taxonomy, snapshot)
    return taxonomy, snapshot


def _schedule_article_automation(factory) -> dict[str, int]:
    counts = {
        "articles_checked": 0,
        "articles_published": 0,
        "articles_rejected": 0,
        "source_topics_proposed": 0,
    }
    # Policy/catalog/source wakes advance in a bounded page outside writer transactions.
    now = utcnow()
    expand_automation_demands(factory, now)
    # Access policy is independent of AI mode, source enablement and the AI date window.
    counts["articles_rejected"] += reject_known_paywalled_articles(factory, now=now)
    origin = (
        select(ArticleOrigin.id)
        .join(Source)
        .where(
            ArticleOrigin.article_id == Article.id,
            Source.approval_status == "approved",
            Source.enabled.is_(True),
        )
    )
    settings = get_settings()
    dependency_wakes = settings.automation_dependency_wakes_enabled
    batch = settings.article_automation_batch_size or settings.automation_batch_size
    deadline = (
        monotonic() + settings.article_automation_cycle_seconds
        if dependency_wakes and settings.article_automation_batch_size is not None
        else None
    )
    with factory() as session:
        statement = (
            select(Article.id)
            .where(
                Article.review_status.in_(["pending", "approved"]),
                Article.publication_status == "unpublished",
                Article.automation_next_check_at <= now,
            )
            .order_by(Article.automation_next_check_at, Article.id)
            .limit(batch)
        )
        if dependency_wakes:
            # Bound examined due identities before source/date checks. Otherwise
            # an indefinitely ineligible prefix remains hot on every scheduler tick.
            selected = _bounded_due_candidates(
                session,
                statement.add_columns(eligible_articles(), origin.exists()),
                batch,
                interleave=deadline is not None,
            )
        else:
            selected = [
                (identifier, True, True)
                for identifier in session.scalars(
                    statement.where(eligible_articles(), origin.exists())
                ).all()
            ]
    for identifier, date_eligible, source_admitted in selected:
        if deadline is not None and monotonic() >= deadline:
            break
        prepared = (
            _prepare_article_candidates(factory, identifier)
            if date_eligible and source_admitted
            else None
        )
        with factory.begin() as session:
            # Workers acquire job -> source -> article -> taxonomy. Use the same order.
            job = session.scalar(
                select(ArticleAnalysisJob)
                .options(
                    defer(ArticleAnalysisJob.input_snapshot),
                    defer(ArticleAnalysisJob.catalog_snapshot),
                    defer(ArticleAnalysisJob.result),
                    defer(ArticleAnalysisJob.usage),
                )
                .where(ArticleAnalysisJob.article_id == identifier)
                .order_by(ArticleAnalysisJob.created_at.desc(), ArticleAnalysisJob.id.desc())
                .limit(1)
                .with_for_update()
            )
            approved_sources(session, identifier, lock=True)
            article = session.scalar(
                select(Article).where(Article.id == identifier).with_for_update(of=Article)
            )
            if (
                article is None
                or article.review_status not in {"pending", "approved"}
                or article.publication_status != "unpublished"
                or article.automation_next_check_at > now
            ):
                continue
            admitted = any(
                o.source.enabled and o.source.approval_status == "approved" for o in article.origins
            )
            if not eligible_article(article) or not admitted:
                # Park without consuming a publication-policy observation. A
                # source/editorial/settings wake makes this identity due again.
                wait_for_dependency(
                    article,
                    now,
                    reason="source_admission" if not admitted else "content_date_deferred",
                )
                continue
            if not date_eligible or not source_admitted:
                # Eligibility changed since preselection. Prepare current inputs
                # outside publication locks on the next tick.
                continue
            with observe_automation_demand(session, article, now):
                article.automation_started_at = article.automation_started_at or now
                article.automation_next_check_at = now + CHECK_INTERVAL
                wait_for_dependency(article, now)
                counts["articles_checked"] += 1
                reconcile_processing(session, article, taxonomy=prepared[0] if prepared else None)
                if not get_settings().full_automation or article.review_status == "approved":
                    continue
                if job is not None and job.status in {"queued", "running"}:
                    wait_for_job(article, job, now)
                    continue  # Includes provider cooldowns; durable retries own this work.
                enrichment = session.scalar(
                    select(ArticleEnrichmentJob)
                    .where(ArticleEnrichmentJob.article_id == identifier)
                    .order_by(
                        ArticleEnrichmentJob.created_at.desc(), ArticleEnrichmentJob.id.desc()
                    )
                    .limit(1)
                )
                if enrichment is not None and enrichment.status in {"queued", "running"}:
                    wait_for_job(article, enrichment, now)
                    continue
                if enrichment is not None and enrichment.status == "failed":
                    content = session.get(ArticleContent, identifier)
                    if not meaningful_text(article.summary) and not meaningful_text(
                        content.text if content is not None else None
                    ):
                        decide_article(
                            session,
                            identifier,
                            EditorialDecision(
                                action="reject",
                                actor="DevFeed automation",
                                note=(
                                    "Article enrichment failed after retry exhaustion; "
                                    "no publisher summary or article text is available. "
                                    f"{enrichment.error or ''}"
                                )[:1000],
                                expected_revision=article.editorial_revision,
                            ),
                            automation={
                                "policy": "article-enrichment-failure-v1",
                                "job_id": str(enrichment.id),
                                "error": enrichment.error,
                            },
                        )
                        counts["articles_rejected"] += 1
                        continue
                if job is None and enrichment is None:
                    request_article_enrichment(session, identifier, automatic=True)
                    continue
                negative = (
                    job is not None
                    and job.status == "succeeded"
                    and (
                        job.result.get("developer_relevance") == "unrelated"
                        or job.result.get("page_kind") == "non_article"
                    )
                )
                if not negative:
                    counts["source_topics_proposed"] += propose_source_topics(session, article)
                lock_topics(session, read=True)
                snapshot = source_snapshot(article, session.get(ArticleContent, identifier))
                if (
                    prepared is None
                    or snapshot != prepared[1]
                    or not snapshot_current(session, ("topics", "tags"), prepared[0])
                ):
                    # Catalog/input changes must be ranked again outside these locks.
                    # Leave the article due for the next tick rather than using stale data.
                    article.automation_next_check_at = now
                    article.automation_wait_reason = "changed_during_evaluation"
                    continue
                taxonomy = prepared[0]
                evaluation = None
                if negative:
                    decision = apply_publication_policy(session, article, job, taxonomy=taxonomy)
                    if decision["status"] == "rejected":
                        counts["articles_rejected"] += 1
                        continue
                # Re-extract only records affected by the old description/code/truncation
                # bugs. The new job is stamped at creation, including terminal failures,
                # so unchanged pages cannot create an automatic enrichment loop.
                if (
                    enrichment is not None
                    and enrichment.status == "succeeded"
                    and enrichment.result.get("extraction_version") != EXTRACTION_VERSION
                    and (
                        enrichment.result.get("text_source") in {None, "description"}
                        or enrichment.result.get("sample_characters") == 2500
                    )
                    and (job is None or job.outcome == "insufficient_evidence")
                ):
                    request_article_enrichment(session, identifier)
                    continue
                feedback = (job.result or {}).get("validation_feedback", {}) if job else {}
                if (
                    job is not None
                    and job.status == "failed"
                    and job.error == "invalid_analysis_result"
                    and feedback.get("code") == "schema_validation"
                    and feedback.get("fields") == ["result:value_error"]
                    and (job.usage or {}).get("validation_version") != VALIDATION_VERSION
                ):
                    replacement = request_analysis(
                        session, identifier, automatic=True, force=True, taxonomy=taxonomy
                    )
                    if replacement is not None:
                        replacement.usage = {
                            **replacement.usage,
                            "requested_reason": "title_validation_corrected",
                        }
                    continue
                current = job is not None and (
                    job.input_hash == snapshot_hash(snapshot)
                    and job.editorial_revision == article.editorial_revision
                    and analysis_catalog_current(job, taxonomy, snapshot)
                    and job.prompt_version == PROMPT_VERSION
                    and job.outcome not in {"superseded", "content_date_deferred"}
                )
                if not meaningful_text(snapshot["text"]):
                    reasons = ["insufficient_source_text"]
                elif not current:
                    # Editorial revisions must be current even with an unchanged input hash.
                    request_analysis(
                        session, identifier, automatic=True, force=True, taxonomy=taxonomy
                    )
                    continue
                elif job.status == "failed":
                    reasons = ["analysis_failed", job.error or "analysis_attempts_exhausted"]
                elif job.result.get("topic_match_status") == "no_topic_match":
                    reasons = ["no_supported_topic_match"]
                elif job.result.get("topic_match_status") == "no_primary_topic":
                    reasons = ["no_supported_primary_topic"]
                else:
                    evaluation = evaluate_publication(session, article, job, taxonomy=taxonomy)
                    decision = evaluation
                    if decision["status"] == "would_publish":
                        apply_publication_policy(
                            session, article, job, taxonomy=taxonomy, evaluation=evaluation
                        )
                        counts["articles_published"] += 1
                        continue
                    reasons = decision["reasons"]
                    if job.outcome == "insufficient_evidence":
                        reasons = ["insufficient_analysis_evidence"]
                    # Allow matching topic research to finish before recording a blocked decision.
                    if (
                        job.result.get("developer_relevance") != "unrelated"
                        and (
                            "missing_active_primary_topic" in reasons
                            or job.outcome == "insufficient_evidence"
                        )
                        and now < article.automation_started_at + TOPIC_WAIT
                        and pending_topic_matches(session, snapshot)
                    ):
                        wait_for_dependency(
                            article,
                            now,
                            reason="topic_research",
                            deadline=article.automation_started_at + TOPIC_WAIT,
                        )
                        continue
                decision = apply_publication_policy(
                    session,
                    article,
                    job,
                    taxonomy=taxonomy,
                    evaluation=evaluation,
                )
                if decision["status"] == "rejected":
                    counts["articles_rejected"] += 1
    return counts
