"""Replace catalog matching sweeps with transactional, indexed change jobs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

PRODUCT_FIELDS = (
    "revision,updated_at,technologies,evidence,name,description,status,"
    "verified_at,excluded,merged_into_id"
)
PRODUCT_FACT_FIELDS = "revision,technologies,evidence,name,description"
ARTICLE_FIELDS = "title,summary,content_type,publication_status,editorial_revision"
TOPIC_FIELDS = "name,aliases,status,identity_keys,updated_at"


def upgrade():
    op.create_table(
        "partner_match_jobs",
        sa.Column("kind", sa.String(20), primary_key=True),
        sa.Column("entity_id", sa.Uuid(), primary_key=True),
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("cursor", sa.Uuid(), nullable=True),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "kind IN ('product','article','topics')", name="ck_partner_match_job_kind"
        ),
        sa.CheckConstraint("generation > 0", name="ck_partner_match_job_generation"),
    )
    op.create_index(
        "ix_partner_match_job_due", "partner_match_jobs", ["updated_at", "kind", "entity_id"]
    )
    for table in ("articles", "partner_products", "topics"):
        op.add_column(
            table,
            sa.Column(
                "matching_tokens", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"
            ),
        )

    op.create_index(
        "ix_topic_relations_related_topic", "topic_relations", ["related_topic_id", "topic_id"]
    )

    # These deliberately overselect. Exact Python technology boundaries, grounding and
    # task matching remain authoritative. Four Python re.I equivalents normalize to
    # ASCII; Unicode product technologies and topic identities widen candidate discovery.
    # Bound ASCII chunks below GIN entry limits even for long article words. Valid
    # technologies are at most 200 characters; splitting only adds coarse candidates.
    op.execute("""
    CREATE FUNCTION partner_matching_tokens(value text) RETURNS text[]
    LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
      SELECT ARRAY(
        SELECT DISTINCT token FROM (
          SELECT (regexp_matches(lower(translate(coalesce(value, ''), 'İıſK', 'iisk') COLLATE "C"),
                                 '[a-z0-9_]{1,200}', 'g'))[1] AS token
          UNION ALL
          SELECT '__unicode__'
            WHERE octet_length(convert_to(coalesce(value, ''), 'UTF8'))
                  <> char_length(coalesce(value, ''))
        ) AS tokens ORDER BY token
      )
    $$
    """)
    op.execute("""
    CREATE FUNCTION partner_matching_product_tokens(value jsonb) RETURNS text[]
    LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
    DECLARE technology text; current_tokens text[]; tokens text[] := '{}';
    BEGIN
      IF value IS NULL THEN RETURN tokens; END IF;
      IF jsonb_typeof(value) <> 'array' THEN RETURN ARRAY['__any__']; END IF;
      FOR technology IN SELECT jsonb_array_elements_text(value) LOOP
        current_tokens := partner_matching_tokens(technology);
        IF cardinality(current_tokens) = 0 OR '__unicode__' = ANY(current_tokens) THEN
          tokens := tokens || ARRAY['__any__'];
        END IF;
        tokens := tokens || array_remove(current_tokens, '__unicode__');
      END LOOP;
      RETURN ARRAY(SELECT DISTINCT token FROM unnest(tokens) AS token ORDER BY token);
    END $$
    """)
    op.execute("""
    CREATE FUNCTION partner_matching_changed(previous jsonb, next_row jsonb, fields text[])
    RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
      SELECT EXISTS(
        SELECT 1 FROM unnest(fields) AS field
          WHERE previous->field IS DISTINCT FROM next_row->field
      )
    $$
    """)
    op.execute("""
    CREATE FUNCTION partner_matching_enqueue(job_kind text, identifier uuid) RETURNS void
    LANGUAGE sql AS $$
      INSERT INTO partner_match_jobs(kind, entity_id) VALUES (job_kind, identifier)
      ON CONFLICT (kind, entity_id) DO UPDATE SET
        generation = partner_match_jobs.generation + 1,
        cursor = NULL, requested_at = clock_timestamp(), updated_at = clock_timestamp()
    $$
    """)
    op.execute("""
    CREATE FUNCTION partner_matching_project() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_TABLE_NAME = 'partner_products' THEN
        NEW.matching_tokens := partner_matching_product_tokens(NEW.technologies);
        IF TG_OP = 'UPDATE'
           AND partner_matching_changed(to_jsonb(OLD), to_jsonb(NEW),
                                        string_to_array(TG_ARGV[0], ','))
           AND NEW.updated_at IS NOT DISTINCT FROM OLD.updated_at THEN
          NEW.updated_at := clock_timestamp();
        END IF;
      ELSE
        NEW.matching_tokens := partner_matching_tokens(
          coalesce(NEW.title, '') || E'\\n' || coalesce(NEW.summary, ''));
      END IF;
      RETURN NEW;
    END $$
    """)
    op.execute("""
    CREATE FUNCTION partner_matching_source_changed() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'UPDATE'
         AND NOT partner_matching_changed(to_jsonb(OLD), to_jsonb(NEW),
                                          string_to_array(TG_ARGV[1], ',')) THEN
        RETURN NEW;
      END IF;
      PERFORM partner_matching_enqueue(
        TG_ARGV[0], CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END);
      RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END $$
    """)

    # A statement barrier precedes normal row locks, including DELETE's tuple lock.
    # Matching workers use the shared, nonblocking form before installing topic edges.
    op.execute("""
    CREATE FUNCTION partner_matching_taxonomy_barrier() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      PERFORM pg_advisory_xact_lock(832604285);
      RETURN NULL;
    END $$
    """)
    op.execute("""
    CREATE FUNCTION partner_matching_invalidate_topics(topic_ids uuid[], tokens text[])
    RETURNS void LANGUAGE plpgsql AS $$
    BEGIN
      WITH affected AS MATERIALIZED (
        SELECT id FROM partner_products WHERE matching_tokens && tokens
        UNION
        SELECT id FROM partner_products WHERE matching_tokens @> ARRAY['__any__']::text[]
        UNION
        SELECT id FROM partner_products WHERE '__unicode__' = ANY(tokens)
        UNION
        SELECT product_id FROM partner_product_topics WHERE topic_id = ANY(topic_ids)
      ), removed AS (
        DELETE FROM partner_product_topics
          WHERE product_id IN (SELECT id FROM affected) RETURNING product_id
      )
      INSERT INTO partner_match_jobs(kind, entity_id)
        SELECT 'topics', id FROM affected ORDER BY id
      ON CONFLICT (kind, entity_id) DO UPDATE SET
        generation = partner_match_jobs.generation + 1,
        cursor = NULL, requested_at = clock_timestamp(), updated_at = clock_timestamp();
    END $$
    """)
    op.execute("""
    CREATE FUNCTION partner_matching_topic_changed() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE identifiers uuid[] := '{}'; tokens text[] := '{}';
    BEGIN
      IF TG_OP <> 'DELETE' THEN
        NEW.matching_tokens := partner_matching_tokens(
          coalesce(NEW.name, '') || E'\\n' || coalesce(array_to_string(NEW.aliases, E'\\n'), ''));
      END IF;
      IF TG_OP = 'UPDATE' THEN
        IF NOT partner_matching_changed(to_jsonb(OLD), to_jsonb(NEW),
                                        string_to_array(TG_ARGV[0], ',')) THEN
          RETURN NEW;
        END IF;
        IF NEW.updated_at IS NOT DISTINCT FROM OLD.updated_at THEN
          NEW.updated_at := clock_timestamp();
        END IF;
      END IF;
      IF TG_OP <> 'INSERT' THEN
        identifiers := identifiers || OLD.id;
        tokens := tokens || partner_matching_tokens(
          coalesce(OLD.name, '') || E'\\n' || coalesce(array_to_string(OLD.aliases, E'\\n'), ''));
      END IF;
      IF TG_OP <> 'DELETE' THEN
        identifiers := identifiers || NEW.id;
        tokens := tokens || NEW.matching_tokens;
      END IF;
      -- A formerly inactive/broad related target can become useful without having
      -- prepared edges yet. Include both directions before DELETE cascades lose them.
      identifiers := ARRAY(
        SELECT unnest(identifiers)
        UNION
        SELECT topic_id FROM topic_relations
          WHERE topic_id = ANY(identifiers) OR related_topic_id = ANY(identifiers)
        UNION
        SELECT related_topic_id FROM topic_relations
          WHERE topic_id = ANY(identifiers) OR related_topic_id = ANY(identifiers)
      );
      tokens := tokens || ARRAY(
        SELECT DISTINCT unnest(matching_tokens) FROM topics WHERE id = ANY(identifiers)
      );
      PERFORM partner_matching_invalidate_topics(identifiers, tokens);
      RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END $$
    """)
    op.execute("""
    CREATE FUNCTION partner_matching_relation_changed() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE identifiers uuid[] := '{}'; tokens text[];
    BEGIN
      IF TG_OP = 'UPDATE'
         AND NOT partner_matching_changed(to_jsonb(OLD), to_jsonb(NEW),
                                          ARRAY['topic_id','related_topic_id','relation']) THEN
        RETURN NEW;
      END IF;
      IF TG_OP <> 'INSERT' THEN
        identifiers := identifiers || ARRAY[OLD.topic_id, OLD.related_topic_id];
      END IF;
      IF TG_OP <> 'DELETE' THEN
        identifiers := identifiers || ARRAY[NEW.topic_id, NEW.related_topic_id];
      END IF;
      tokens := ARRAY(
        SELECT DISTINCT unnest(matching_tokens) FROM topics WHERE id = ANY(identifiers)
      );
      PERFORM partner_matching_invalidate_topics(identifiers, tokens);
      RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END $$
    """)

    # Linear bootstrap installs indexed source projections before capturing live writes.
    # Existing factual evaluations, attribution assets and delivery history are retained.
    op.execute("""
    UPDATE partner_products SET matching_tokens = partner_matching_product_tokens(technologies)
    """)
    op.execute("""
    UPDATE articles SET matching_tokens = partner_matching_tokens(
      coalesce(title, '') || E'\\n' || coalesce(summary, ''))
    """)
    op.execute("""
    UPDATE topics SET matching_tokens = partner_matching_tokens(
      coalesce(name, '') || E'\\n' || coalesce(array_to_string(aliases, E'\\n'), ''))
    """)
    # Build compact GIN indexes from populated projections rather than filling a
    # pending list during the initial backfill. Normal writes retain fastupdate.
    for table in ("articles", "partner_products", "topics"):
        op.create_index(
            f"ix_{table}_matching_tokens", table, ["matching_tokens"], postgresql_using="gin"
        )
    # Legacy topic rows lack a complete dependency snapshot. Rebuild derived edges
    # rather than letting an old relation or alias survive after sweep expiry is removed.
    op.execute("DELETE FROM partner_product_topics")
    for table, kind, fields in (
        ("partner_products", "product", PRODUCT_FIELDS),
        ("articles", "article", ARTICLE_FIELDS),
    ):
        # Policy changes still queue work, but must preserve the catalog observation
        # timestamp unless matching facts themselves changed without an explicit stamp.
        projection_fields = PRODUCT_FACT_FIELDS if kind == "product" else fields
        op.execute(f"""
        CREATE TRIGGER partner_matching_project
        BEFORE INSERT OR UPDATE ON {table}
        FOR EACH ROW EXECUTE FUNCTION partner_matching_project('{projection_fields}')
        """)
        op.execute(f"""
        CREATE TRIGGER partner_matching_source_changed
        AFTER INSERT OR UPDATE OR DELETE ON {table}
        FOR EACH ROW EXECUTE FUNCTION partner_matching_source_changed('{kind}', '{fields}')
        """)
    for table in ("topics", "topic_relations"):
        op.execute(f"""
        CREATE TRIGGER partner_matching_taxonomy_barrier
        BEFORE INSERT OR UPDATE OR DELETE ON {table}
        FOR EACH STATEMENT EXECUTE FUNCTION partner_matching_taxonomy_barrier()
        """)
    op.execute(f"""
    CREATE TRIGGER partner_matching_topic_changed
    BEFORE INSERT OR UPDATE OR DELETE ON topics
    FOR EACH ROW EXECUTE FUNCTION partner_matching_topic_changed('{TOPIC_FIELDS}')
    """)
    op.execute("""
    CREATE TRIGGER partner_matching_relation_changed
    BEFORE INSERT OR UPDATE OR DELETE ON topic_relations
    FOR EACH ROW EXECUTE FUNCTION partner_matching_relation_changed()
    """)
    op.execute("""
    INSERT INTO partner_match_jobs(kind, entity_id) SELECT 'product', id FROM partner_products
    """)
    op.drop_index("ix_partner_match_states_updated_at", table_name="partner_match_states")
    op.drop_table("partner_match_states")


def downgrade():
    for table in ("partner_products", "articles"):
        op.execute(f"DROP TRIGGER partner_matching_source_changed ON {table}")
        op.execute(f"DROP TRIGGER partner_matching_project ON {table}")
    op.execute("DROP TRIGGER partner_matching_topic_changed ON topics")
    op.execute("DROP TRIGGER partner_matching_relation_changed ON topic_relations")
    for table in ("topics", "topic_relations"):
        op.execute(f"DROP TRIGGER partner_matching_taxonomy_barrier ON {table}")
    for function in (
        "partner_matching_relation_changed()",
        "partner_matching_topic_changed()",
        "partner_matching_invalidate_topics(uuid[], text[])",
        "partner_matching_taxonomy_barrier()",
        "partner_matching_source_changed()",
        "partner_matching_project()",
        "partner_matching_enqueue(text, uuid)",
        "partner_matching_changed(jsonb, jsonb, text[])",
        "partner_matching_product_tokens(jsonb)",
        "partner_matching_tokens(text)",
    ):
        op.execute(f"DROP FUNCTION {function}")
    for table in ("articles", "partner_products", "topics"):
        op.drop_index(f"ix_{table}_matching_tokens", table_name=table)
        op.drop_column(table, "matching_tokens")
    op.drop_index("ix_topic_relations_related_topic", table_name="topic_relations")
    op.drop_index("ix_partner_match_job_due", table_name="partner_match_jobs")
    op.drop_table("partner_match_jobs")
    op.create_table(
        "partner_match_states",
        sa.Column(
            "product_id",
            sa.Uuid(),
            sa.ForeignKey("partner_products.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("product_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("product_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("topic_generation", sa.String(64), nullable=False, server_default=""),
        sa.Column("article_cursor", sa.Uuid(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_partner_match_states_updated_at", "partner_match_states", ["updated_at"])
