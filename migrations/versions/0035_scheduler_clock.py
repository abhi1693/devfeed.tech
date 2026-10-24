"""Maintain article recheck clocks without invalidating complete pipeline cards."""

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

# Keep every card dependency from 0034 except the scheduler's informational clock.
ARTICLE_COLUMNS = (
    "id,canonical_url,title,summary,ai_title,ai_summary,ai_description,review_status,"
    "publication_status,language,content_type,content_format,classification_provenance,"
    "metadata_source_type,published_at,image_url,managed_image,discovered_at,"
    "editorial_revision,published_to_feed_at"
)


def _article_capture(columns):
    op.execute("DROP TRIGGER read_model_changed ON articles")
    op.execute(f"""CREATE TRIGGER read_model_changed
      AFTER INSERT OR UPDATE OF {columns} OR DELETE ON articles
      FOR EACH ROW EXECUTE FUNCTION devfeed_read_model_changed('article_entity')""")


def upgrade():
    _article_capture(ARTICLE_COLUMNS)
    op.execute("""
    CREATE FUNCTION devfeed_read_model_article_clock() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
      policies text[];
    BEGIN
      -- At most the serving and building generations can become visible. Supplying
      -- their keys uses the existing projection primary key rather than scanning
      -- all saved article cards or accumulating an event per scheduler recheck.
      SELECT ARRAY[payload->>'target_policy', payload->>'serving_policy']
        INTO policies FROM read_model_states WHERE name = 'pipeline';
      UPDATE pipeline_projections
        SET payload = jsonb_set(payload, '{next_check_at}',
                                to_jsonb(NEW.automation_next_check_at), true)
        WHERE policy_version = ANY(policies) AND kind = 'article'
          AND entity_id = NEW.id AND state <> 'complete';
      -- Retry transitions come from durable jobs, not the scheduler recheck clock.
      RETURN NEW;
    END $$
    """)
    op.execute("""CREATE TRIGGER read_model_article_clock_changed
      AFTER UPDATE OF automation_next_check_at ON articles
      FOR EACH ROW WHEN (OLD.automation_next_check_at IS DISTINCT FROM NEW.automation_next_check_at)
      EXECUTE FUNCTION devfeed_read_model_article_clock()""")


def downgrade():
    op.execute("DROP TRIGGER read_model_article_clock_changed ON articles")
    op.execute("DROP FUNCTION devfeed_read_model_article_clock()")
    _article_capture(ARTICLE_COLUMNS + ",automation_next_check_at")
