"""Install narrow native capture without exporting private current-card inputs."""

from devfeed_core.analytics_schema import TABLES, TOKEN_FIELDS, WATCH_FIELDS


def _different(fields):
    return " OR ".join(f'OLD."{name}" IS DISTINCT FROM NEW."{name}"' for name in fields)


def _record(table, fields, alias):
    values: list[str] = []
    for name in fields:
        value = f'{alias}."{name}"'
        if name == "automation":
            value = (
                f"CASE WHEN {value} = '{{}}'::jsonb THEN '{{}}'::jsonb "
                "ELSE jsonb_build_object('set', true) END"
            )
        elif name in {"usage", "tokens"} and table != "topic_decision_calls":
            value = (
                "jsonb_build_object("
                + ", ".join(
                    f"'{key}', CASE WHEN jsonb_typeof({value}->'{key}') "
                    f"IN ('string','number') THEN left({value}->>'{key}',19) END"
                    for key in TOKEN_FIELDS
                )
                + ")"
            )
        values.extend((f"'{name}'", value))
    return "jsonb_build_object(" + ", ".join(values) + ")"


def capture_statements(sources, watch_fields):
    """Return complete V3 trigger DDL for a source schema, without executing it."""
    result: list[str] = []
    for table in sources:
        result.extend(
            (
                f"DROP TRIGGER IF EXISTS analytics_capture ON {table}",
                f"DROP TRIGGER IF EXISTS analytics_capture_truncate ON {table}",
                f"DROP FUNCTION IF EXISTS analytics_capture_{table}()",
            )
        )
    result.extend(
        (
            "DROP FUNCTION IF EXISTS analytics_capture_truncate()",
            """CREATE FUNCTION analytics_capture_truncate() RETURNS trigger
              LANGUAGE plpgsql AS $body$ BEGIN
                IF EXISTS (SELECT 1 FROM analytics_pipeline_state WHERE enabled) THEN
                  INSERT INTO analytics_ingest_events(source_table,operation,record)
                    VALUES (TG_TABLE_NAME,'T','{}'::jsonb);
                END IF;
                RETURN NULL;
              END $body$""",
        )
    )
    for table, spec in sources.items():
        before = _record(table, spec["fields"], "OLD")
        after = _record(table, spec["fields"], "NEW")
        changed = _different(watch_fields[table])
        result.append(f"""CREATE FUNCTION analytics_capture_{table}() RETURNS trigger
          LANGUAGE plpgsql AS $body$
          DECLARE before_value jsonb; after_value jsonb; action char(1);
          BEGIN
            IF NOT EXISTS (SELECT 1 FROM analytics_pipeline_state WHERE enabled) THEN
              RETURN NULL;
            END IF;
            IF TG_OP = 'UPDATE' AND NOT ({changed}) THEN RETURN NULL; END IF;
            IF TG_OP <> 'INSERT' THEN before_value := {before}; END IF;
            IF TG_OP <> 'DELETE' THEN after_value := {after}; END IF;
            action := CASE WHEN TG_OP='DELETE' THEN 'D' ELSE 'I' END;
            IF TG_TABLE_NAME='article_opens' AND TG_OP='DELETE'
              AND current_setting('devfeed.analytics_retention_delete',true)='on' THEN
              action := 'I'; after_value := before_value; before_value := NULL;
            END IF;
            INSERT INTO analytics_ingest_events(source_table,operation,record,before_record)
              VALUES ('{table}',action,coalesce(after_value,before_value),before_value);
            RETURN NULL;
          END $body$""")
        columns = ", ".join(f'"{name}"' for name in watch_fields[table])
        result.extend(
            (
                f"CREATE TRIGGER analytics_capture AFTER INSERT OR UPDATE OF {columns} "
                f"OR DELETE ON {table} FOR EACH ROW "
                f"EXECUTE FUNCTION analytics_capture_{table}()",
                f"CREATE TRIGGER analytics_capture_truncate AFTER TRUNCATE ON {table} "
                "FOR EACH STATEMENT EXECUTE FUNCTION analytics_capture_truncate()",
            )
        )
    return tuple(result)


def native_capture_statements():
    """Current metadata-only fixtures use the same capture contract as migrations."""
    sources = {name: {"fields": spec.fields, "keys": spec.keys} for name, spec in TABLES.items()}
    return capture_statements(sources, WATCH_FIELDS)
