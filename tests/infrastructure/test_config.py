import pytest
from devfeed_core.config import Settings
from pydantic import ValidationError

DATABASE_URL = "postgresql+psycopg://operator:test-secret@database.invalid/devfeed"
REDIS_URL = "redis://redis.invalid/4"


def test_taxonomy_auto_approval_is_opt_in_and_independently_configurable(monkeypatch):
    keys = ("DEVFEED_AUTO_APPROVE_TOPICS", "DEVFEED_AUTO_APPROVE_TOPIC_RELATIONSHIPS")
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None, database_url=DATABASE_URL, redis_url=REDIS_URL)
    assert settings.auto_approve_topics is False
    assert settings.auto_approve_topic_relationships is False
    for key, attribute in zip(
        keys, ("auto_approve_topics", "auto_approve_topic_relationships"), strict=True
    ):
        monkeypatch.setenv(key, "true")
        settings = Settings(_env_file=None, database_url=DATABASE_URL, redis_url=REDIS_URL)
        assert getattr(settings, attribute) is True
        other = (
            "auto_approve_topic_relationships"
            if attribute == "auto_approve_topics"
            else "auto_approve_topics"
        )
        assert getattr(settings, other) is False
        monkeypatch.setenv(key, "not-a-boolean")
        with pytest.raises(ValidationError):
            Settings(_env_file=None, database_url=DATABASE_URL, redis_url=REDIS_URL)
        monkeypatch.delenv(key)


@pytest.fixture
def no_connection_env(monkeypatch):
    monkeypatch.delenv("DEVFEED_DATABASE_URL", raising=False)
    monkeypatch.delenv("DEVFEED_REDIS_URL", raising=False)


@pytest.mark.parametrize(
    ("values", "missing"),
    [
        ({}, {"database_url", "redis_url"}),
        ({"database_url": DATABASE_URL}, {"redis_url"}),
        ({"redis_url": REDIS_URL}, {"database_url"}),
    ],
)
def test_connections_are_required(no_connection_env, values, missing):
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, **values)
    assert {error["loc"][0] for error in caught.value.errors()} == missing
    assert all(error["type"] == "missing" for error in caught.value.errors())
    assert "test-secret" not in str(caught.value)


@pytest.mark.parametrize("field", ["database_url", "redis_url"])
@pytest.mark.parametrize("blank", ["", "  \t\n"])
def test_blank_connections_are_rejected(no_connection_env, field, blank):
    values = {"database_url": DATABASE_URL, "redis_url": REDIS_URL, field: blank}
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, **values)
    assert [error["loc"] for error in caught.value.errors()] == [(field,)]


def test_explicit_environment_connections_are_used(monkeypatch):
    monkeypatch.setenv("DEVFEED_DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("DEVFEED_REDIS_URL", REDIS_URL)
    settings = Settings(_env_file=None)
    assert settings.database_url == DATABASE_URL
    assert settings.redis_url == REDIS_URL


@pytest.mark.parametrize("log_format", ["text", "json"])
def test_logging_formats_and_normalized_level(log_format):
    settings = Settings(
        _env_file=None,
        database_url=DATABASE_URL,
        redis_url=REDIS_URL,
        log_format=log_format,
        log_level=" debug ",
    )
    assert settings.log_format == log_format and settings.log_level == "DEBUG"


@pytest.mark.parametrize("values", [{"log_format": "xml"}, {"log_level": "VERBOSE"}])
def test_invalid_logging_configuration_fails_validation(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=DATABASE_URL, redis_url=REDIS_URL, **values)


@pytest.mark.parametrize(
    "values",
    [
        {"job_log_max_entries": 99},
        {"job_log_max_entries": 10001},
        {"job_log_ttl_seconds": 3599},
        {"job_log_ttl_seconds": 2592001},
    ],
)
def test_invalid_job_log_retention_is_rejected(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=DATABASE_URL, redis_url=REDIS_URL, **values)


@pytest.mark.parametrize(
    "values",
    [
        {"page_max_bytes": 0},
        {"page_max_bytes": 5_000_001},
        {"article_page_max_bytes": 0},
        {"article_page_max_bytes": 20_000_001},
        {"source_page_max_bytes": 0},
        {"source_page_max_bytes": 20_000_001},
        {"page_timeout_seconds": 0},
        {"page_timeout_seconds": 31},
    ],
)
def test_invalid_page_limits_are_rejected(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=DATABASE_URL, redis_url=REDIS_URL, **values)


@pytest.mark.parametrize(
    "values",
    [
        {"cache_ttl_seconds": 0},
        {"cache_ttl_seconds": 3601},
        {"cache_metadata_ttl_seconds": 0},
        {"cache_metadata_ttl_seconds": 3601},
        {"cache_max_bytes": 0},
        {"cache_max_bytes": 5_000_001},
    ],
)
def test_invalid_cache_limits_are_rejected(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=DATABASE_URL, redis_url=REDIS_URL, **values)
