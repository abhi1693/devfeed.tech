"""Shared CLI execution, JSON output and sanitized operational error handling."""

import json
import logging
import sys
import time
import uuid
from dataclasses import dataclass
from types import SimpleNamespace

import typer
from alembic.util.exc import CommandError
from devfeed_core.cache import CacheUnavailable
from devfeed_core.categories import CategoryNotFound, InvalidCategoryTree
from devfeed_core.config import get_settings
from devfeed_core.db import get_engine
from devfeed_core.feeds.validation import FeedValidationError
from devfeed_core.logging import configure_logging, elapsed_ms, log_context
from devfeed_core.services import OperationConflict, RecordNotFound
from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from devfeed_cli.commands import InputError

logger = logging.getLogger(__name__)


@dataclass
class Invocation:
    code: int | None = None


def invoke(ctx: typer.Context, handler, values: dict, **overrides) -> None:
    """Adapt validated Typer parameters to the existing shared operation handlers."""
    command = ctx.info_name
    action = None
    if ctx.parent is not None and ctx.parent.parent is not None:
        command, action = ctx.parent.info_name, command
    # Typer converts e.g. paths and repeated options when calling the callback;
    # ctx.params still contains transport strings/tuples. Use callback values.
    args = SimpleNamespace(
        **{key: value for key, value in {**values, **overrides}.items() if key != "ctx"},
        command=command,
        action=action,
        execute=handler,
    )
    configure_logging("cli")
    with log_context(
        service="cli",
        command_id=str(uuid.uuid4()),
        command=args.command,
        action=getattr(args, "action", None),
        source_type=getattr(args, "source_type", None),
    ):
        started = time.perf_counter()
        code = execute(args)
        logger.log(
            logging.INFO if code == 0 else logging.WARNING,
            "command_completed",
            extra={"exit_code": code, "duration_ms": elapsed_ms(started)},
        )
        ctx.ensure_object(Invocation).code = code
        raise typer.Exit(code)


def execute(args) -> int:
    try:
        settings = get_settings()
        configure_logging("cli", settings.log_level, settings.log_format)
        logger.info("command_started")
        result = args.execute(args)
        fields = {}
        if isinstance(result, dict):
            for key in ("submitted", "enabled"):
                if key in result:
                    fields[key] = result[key]
            if "created" in result:
                fields["sources_created"] = int(result["created"])
            id_field = {
                "sources": "source_id",
                "jobs": "job_id",
                "images": "job_id",
                "articles": "job_id",
                "categories": "category_id",
                "tags": "tag_id",
            }.get(args.command)
            if args.command == "sources" and args.action in {
                "fetch",
                "enrich",
                "enrichment-dispatch",
            }:
                id_field = "job_id"
            if id_field and "id" in result:
                fields[id_field] = result["id"]
            if "source_id" in result:
                fields["source_id"] = result["source_id"]
            if "article_id" in result:
                fields["article_id"] = result["article_id"]
            for resource in ("source", "job"):
                if isinstance(result.get(resource), dict):
                    fields[f"{resource}_id"] = result[resource].get("id")
        if result is not None:
            print(json.dumps(result, indent=2, default=str))
        if args.command == "status" and not result["dependencies_ready"]:
            logger.warning("command_dependencies_unavailable")
            return 1
        logger.info("command_succeeded", extra=fields)
        return 0
    except CacheUnavailable:
        logger.warning("command_dependencies_unavailable")
        print("error: Response cache unavailable; unable to complete cache clear.", file=sys.stderr)
        return 1
    except ValidationError as exc:
        logger.warning("command_rejected", extra={"error_type": type(exc).__name__})
        details = "; ".join(
            f"{'.'.join(map(str, item['loc']))}: {item['msg']}" for item in exc.errors()
        )
        print(f"error: {details}", file=sys.stderr)
        return 2
    except (
        InputError,
        FeedValidationError,
        CategoryNotFound,
        InvalidCategoryTree,
        RecordNotFound,
        OperationConflict,
    ) as exc:
        logger.warning("command_rejected", extra={"error_type": type(exc).__name__})
        if isinstance(exc, FeedValidationError) and exc.upstream_status == 404:
            print(
                "error: Feed validation failed: HTTP 404 (not found). Check the RSS/Atom URL.",
                file=sys.stderr,
            )
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2
    except IntegrityError as exc:
        logger.warning("command_conflict", extra={"error_type": type(exc).__name__})
        print("error: Conflicting or invalid record; no changes committed.", file=sys.stderr)
        return 2
    except SQLAlchemyError:
        logger.exception("database_operation_failed")
        print(
            "error: Database operation failed. Check connectivity and configuration; "
            "apply pending migrations with 'devfeed db upgrade'.",
            file=sys.stderr,
        )
        return 1
    except RedisError:
        logger.exception("redis_operation_failed")
        print(
            "error: Redis is unavailable. Check DEVFEED_REDIS_URL and connectivity.",
            file=sys.stderr,
        )
        return 1
    except CommandError:
        logger.exception("migration_failed")
        print(
            "error: Migration command failed. Check the migration configuration and schema.",
            file=sys.stderr,
        )
        return 1
    except (OSError, UnicodeError):
        logger.exception("command_io_failed")
        print(
            "error: Unable to read the requested file or access a required service.",
            file=sys.stderr,
        )
        return 1
    except ValueError:
        logger.warning("command_rejected", extra={"error_type": "ValueError"})
        # Connection parsers can include credentials in their exception messages.
        print("error: Invalid command input or connection configuration.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        logger.info("command_interrupted")
        return 130
    except Exception:
        logger.exception("command_failed")
        print("error: Unexpected command failure; see diagnostic logs.", file=sys.stderr)
        return 1
    finally:
        if get_engine.cache_info().currsize:
            get_engine().dispose()
