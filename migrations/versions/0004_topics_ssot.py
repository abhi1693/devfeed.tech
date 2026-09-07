"""Consolidate subject taxonomy into topics, retaining original records in an audit archive."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0004_topics_ssot"
down_revision = "0003_category_proposals"
branch_labels = None
depends_on = None


def terms(row):
    return {v.strip().casefold() for v in [row["name"], row["slug"], *row.get("aliases", [])]}


def unique(values):
    return list({v.casefold(): v for v in values}.values())


def draft(row):
    return {
        key: row[key]
        for key in (
            "name",
            "slug",
            "kind",
            "aliases",
            "keywords",
            "description",
            "website_url",
            "logo_url",
            "facts",
        )
    }


def snapshot(row):
    return {"id": str(row["id"]), "status": row["status"], "draft": draft(row)}


def merge_assignments(bind, source_query, params, evidence):
    # A manual non-membership decision conflicts with legacy feed membership.
    # Preserve both records for an operator rather than overwrite either intent.
    conflict = bind.scalar(
        sa.text(f"""
        SELECT existing.article_id FROM article_topics existing
        JOIN ({source_query}) incoming ON incoming.article_id = existing.article_id
        WHERE existing.topic_id = :topic AND existing.origin = 'manual'
          AND existing.role IN ('comparison', 'incidental')
        LIMIT 1
    """),
        params,
    )
    if conflict is not None:
        raise RuntimeError(
            f"Reconcile the manual topic role for article {conflict}, topic {params['topic']} "
            "with its category/tag membership before migrating"
        )
    bind.execute(
        sa.text(f"""
        INSERT INTO article_topics (article_id, topic_id, role, relevance, evidence, origin)
        SELECT article_id, :topic, 'supporting', 1, :evidence,
               CASE WHEN bool_or(origin = 'manual') THEN 'manual' ELSE min(origin) END
        FROM ({source_query}) incoming GROUP BY article_id
        ON CONFLICT (article_id, topic_id) DO UPDATE SET
            role = CASE WHEN article_topics.role = 'primary' THEN 'primary' ELSE 'supporting' END,
            relevance = greatest(article_topics.relevance, EXCLUDED.relevance),
            evidence = article_topics.evidence || chr(10) || EXCLUDED.evidence,
            origin = CASE WHEN EXCLUDED.origin = 'manual' THEN 'manual'
                          ELSE article_topics.origin END
        WHERE article_topics.origin <> 'manual'
    """),
        {**params, "evidence": evidence},
    )


def upgrade():
    bind = op.get_bind()
    # Hold the same topic lock as the upcoming DDL while planning the merge.
    bind.execute(sa.text("LOCK TABLE topics, categories IN ACCESS EXCLUSIVE MODE"))
    metadata = sa.MetaData()
    topics_table = sa.Table("topics", metadata, autoload_with=bind)
    categories = [
        dict(r) for r in bind.execute(sa.text("SELECT * FROM categories ORDER BY slug")).mappings()
    ]
    topics = {
        r["id"]: dict(r, keywords=[]) for r in bind.execute(sa.select(topics_table)).mappings()
    }
    original_ids = set(topics)
    mapping = {}
    for category in categories:
        matches = {t["id"] for t in topics.values() if terms(t) & terms(category)}
        if category["topic_id"]:
            matches.add(category["topic_id"])
        if len(matches) > 1:
            raise RuntimeError(
                f"Resolve overlapping topic identities for category '{category['slug']}' "
                "before migrating"
            )
        target = topics[next(iter(matches))] if matches else None
        if target:
            if target["status"] != "active":
                raise RuntimeError(
                    f"Review the inactive topic matching category '{category['slug']}' "
                    "before migrating"
                )
            aliases = unique([*target["aliases"], category["name"], category["slug"]])
            aliases = [
                v
                for v in aliases
                if v.casefold() not in {target["name"].casefold(), target["slug"].casefold()}
            ]
            keywords = unique([*target["keywords"], *category["keywords"]])
            if len(aliases) > 50 or len(keywords) > 100:
                raise RuntimeError(
                    f"Consolidate aliases/keywords for '{category['slug']}' before migrating"
                )
            target.update(
                aliases=aliases,
                keywords=keywords,
                description=target["description"] or category["description"],
            )
        else:
            if category["id"] in topics:
                raise RuntimeError("Reconcile category/topic UUID collisions before migrating")
            target = dict(
                id=category["id"],
                name=category["name"],
                slug=category["slug"],
                kind="discipline",
                aliases=[],
                keywords=category["keywords"],
                status="active",
                description=category["description"],
                ai_description=None,
                website_url=None,
                logo_url=None,
                facts=[],
            )
            topics[target["id"]] = target
        mapping[category["id"]] = target["id"]
    # Preflight the combined active catalog before any DDL or data changes. This
    # revision keeps the classifier's 500-entry contract; never truncate subjects.
    active_count = sum(topic["status"] == "active" for topic in topics.values())
    if active_count > 500:
        raise RuntimeError(
            f"Topic consolidation would create {active_count} active topics; analysis "
            "supports at most 500. Reconcile overlapping categories/topics and review "
            "unused active topics before retrying. No taxonomy changes were applied."
        )
    op.add_column(
        "topics",
        sa.Column("keywords", pg.ARRAY(sa.String(100)), nullable=False, server_default="{}"),
    )
    # Raw historical records are retained independently of the runtime catalog.
    op.create_table(
        "taxonomy_migration_archive",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_table", sa.String(50), nullable=False),
        sa.Column("record", pg.JSONB(), nullable=False),
    )
    for table in (
        "categories",
        "category_proposals",
        "article_categories",
        "article_topics",
        "tags",
    ):
        bind.execute(
            sa.text(
                "INSERT INTO taxonomy_migration_archive (source_table, record) "
                f"SELECT :table, to_jsonb(t) FROM {table} t"
            ),
            {"table": table},
        )
    # Reflect the newly added keywords column only after the preflight passes.
    topics_table = sa.Table("topics", sa.MetaData(), autoload_with=bind)
    for target in topics.values():
        if target["id"] in original_ids:
            bind.execute(
                topics_table.update()
                .where(topics_table.c.id == target["id"])
                .values(
                    aliases=target["aliases"],
                    keywords=target["keywords"],
                    description=target["description"],
                )
            )
        else:
            bind.execute(
                topics_table.insert().values(
                    **target, created_at=sa.func.now(), updated_at=sa.func.now()
                )
            )
    # Preserve hierarchy as graph edges. Feed membership remains a direct assignment.
    for category in categories:
        child = mapping[category["id"]]
        parent = mapping.get(category["parent_id"])
        if parent and parent != child:
            bind.execute(
                sa.text(
                    "INSERT INTO topic_relations (topic_id, related_topic_id, relation) "
                    "VALUES (:child, :parent, 'part_of') ON CONFLICT DO NOTHING"
                ),
                dict(child=child, parent=parent),
            )
        merge_assignments(
            bind,
            "SELECT article_id, origin FROM article_categories WHERE category_id = :category",
            dict(topic=child, category=category["id"]),
            "Migrated explicit category assignment",
        )
        merge_assignments(
            bind,
            "SELECT at.article_id, at.origin FROM article_tags at "
            "JOIN tags t ON t.id = at.tag_id WHERE t.category_id = :category",
            dict(topic=child, category=category["id"]),
            "Migrated tag category membership",
        )
        bind.execute(
            sa.text(
                "UPDATE tags SET topic_id = :topic "
                "WHERE category_id = :category AND topic_id IS NULL"
            ),
            dict(topic=child, category=category["id"]),
        )
    op.create_table(
        "topic_proposals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), sa.ForeignKey("topics.id", ondelete="SET NULL")),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("origin", sa.String(30), nullable=False),
        sa.Column("source_name", sa.String(200), nullable=False),
        sa.Column("proposed", pg.JSONB(), nullable=False),
        sa.Column("baseline", pg.JSONB()),
        sa.Column("evidence", pg.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", pg.JSONB(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", pg.JSONB()),
        sa.Column("review_note", sa.String(1000)),
        sa.Column("applied", pg.JSONB()),
        sa.CheckConstraint("status IN ('pending','approved','rejected')"),
        sa.CheckConstraint("action IN ('create','update')"),
        sa.CheckConstraint("origin IN ('import','article_enrichment','ai_analysis')"),
        sa.CheckConstraint(
            "status = 'pending' OR (reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL)"
        ),
        sa.CheckConstraint("status <> 'approved' OR applied IS NOT NULL"),
    )
    proposals_table = sa.Table("topic_proposals", metadata, autoload_with=bind)
    for old in bind.execute(
        sa.text("SELECT * FROM category_proposals ORDER BY created_at, id")
    ).mappings():
        row = dict(old)
        category_id = row.pop("category_id")
        legacy = next((c for c in categories if c["id"] == category_id), None)
        topic_id = mapping.get(category_id)
        target = topics.get(topic_id)

        def convert(value, target=target, legacy=legacy):
            fields = dict(
                name=value["name"],
                slug=value["slug"],
                kind="discipline",
                aliases=[],
                keywords=value.get("keywords", []),
                description=value.get("description"),
                website_url=None,
                logo_url=None,
                facts=[],
            )
            if target:
                # Existing topic identity and sourced metadata remain authoritative.
                fields = {
                    **draft(target),
                    "keywords": unique([*target["keywords"], *fields["keywords"]]),
                    "description": fields["description"] or target["description"],
                }
                if legacy and target["name"] == legacy["name"] and target["slug"] == legacy["slug"]:
                    fields.update(name=value["name"], slug=value["slug"])
            if len(fields["keywords"]) > 100:
                raise RuntimeError("Consolidate pending proposal keywords before migrating")
            return fields

        row["topic_id"] = topic_id
        row["proposed"] = convert(row["proposed"])
        row["slug"] = row["proposed"]["slug"]
        old_baseline = row["baseline"]
        row["baseline"] = snapshot(target) if target else None
        if legacy and old_baseline and row["baseline"]:
            previous = old_baseline.get("draft", {})
            if any(
                previous.get(k) != legacy[k] for k in ("name", "slug", "description", "keywords")
            ):
                row["baseline"]["legacy_stale"] = True

        if row["applied"]:
            row["applied"] = {
                "id": str(topic_id) if topic_id else row["applied"]["id"],
                "status": "active",
                "draft": convert(row["applied"]["draft"]),
            }
        row["evidence"] = [
            *row["evidence"],
            {"migration": revision, "legacy_proposal_id": str(row["id"])},
        ]
        bind.execute(proposals_table.insert().values(**row))
    # Duplicate pending targets require operator reconciliation, never silently discard a review.
    for field in ("batch_id", "topic_id", "status"):
        op.create_index(f"ix_topic_proposals_{field}", "topic_proposals", [field])
    for label, field in (("slug", "slug"), ("target", "topic_id")):
        op.create_index(
            f"uq_topic_proposal_pending_{label}",
            "topic_proposals",
            [field],
            unique=True,
            postgresql_where=sa.text("status = 'pending'"),
        )
    op.drop_table("category_proposals")
    op.drop_table("article_categories")
    op.drop_column("tags", "category_id")
    op.drop_table("categories")
    # Completed AI job inputs/results remain untouched. Queued work adopts the new contract
    # when claimed by the new worker; active leases recover through the existing retry path.


def downgrade():
    raise RuntimeError(
        "Topic consolidation is forward-only. Restore a pre-migration backup to roll back "
        "without losing subsequent topic edits."
    )
