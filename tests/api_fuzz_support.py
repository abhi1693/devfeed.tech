"""Synthetic records and real Redis sessions for API security and fuzzing tests."""

import importlib
import json
import secrets
import time
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass

from devfeed_core.config import get_settings
from devfeed_core.models import (
    Article,
    ArticleOrigin,
    ArticleTag,
    ArticleTopic,
    Source,
    Tag,
    Topic,
    UserAccount,
)
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy.orm import Session, sessionmaker

READER_ORIGIN = "https://reader.fuzz.invalid"
ADMIN_ORIGIN = "https://admin.fuzz.invalid"
ISSUER = "https://identity.fuzz.invalid"


@dataclass
class SecurityContext:
    apps: dict
    clients: dict
    readers: list[dict]
    admin: dict
    store: Redis
    database: sessionmaker[Session]
    article_id: uuid.UUID
    topic_id: uuid.UUID
    source_id: uuid.UUID
    tag_id: uuid.UUID

    def headers(self, service="user", reader=0):
        return self.admin["headers"] if service == "admin" else self.readers[reader]["headers"]

    def request(self, method, path, *, service="user", reader=0, headers=None, **kwargs):
        supplied = self.headers(service, reader) if headers is None else headers
        return self.clients[service].request(method, path, headers=supplied, **kwargs)


def store_identity(store, module, settings, identity):
    token = secrets.token_urlsafe(32)
    record = {
        **identity,
        "expires_at": int(time.time()) + 3600,
        "absolute_expires_at": int(time.time()) + 7200,
        "policy": module.oidc.policy_key(settings),
    }
    store.set(module.key("session", token), json.dumps(record), ex=3600)
    return token


@contextmanager
def security_context(database, monkeypatch):
    from devfeed_admin_api import auth as admin_auth
    from devfeed_user_api import auth as user_auth

    for name, value in {
        "DEVFEED_USER_BASE_URL": READER_ORIGIN,
        "DEVFEED_USER_OIDC_ISSUER_URL": ISSUER,
        "DEVFEED_USER_OIDC_CLIENT_ID": "fuzz-reader",
        "DEVFEED_USER_OIDC_ORGANIZATION_ID": "fuzz-org",
        "DEVFEED_ADMIN_BASE_URL": ADMIN_ORIGIN,
        "DEVFEED_OIDC_ISSUER_URL": ISSUER,
        "DEVFEED_OIDC_CLIENT_ID": "fuzz-admin",
        "DEVFEED_OIDC_ORGANIZATION_ID": "fuzz-org",
        "OTEL_SDK_DISABLED": "true",
    }.items():
        monkeypatch.setenv(name, value)
    for module in (admin_auth, user_auth):
        module.get_settings.cache_clear()
    store = Redis.from_url(get_settings().redis_url)
    article_id, topic_id, source_id, tag_id = (uuid.uuid4() for _ in range(4))
    readers = []
    with database.begin() as session:
        for number in range(2):
            user_id = uuid.uuid4()
            identity = {
                "user_id": str(user_id),
                "subject": f"fuzz-reader-{number}",
                "issuer": ISSUER,
                "organization_id": "fuzz-org",
                "csrf_token": secrets.token_urlsafe(32),
            }
            session.add(
                UserAccount(
                    id=user_id,
                    subject=identity["subject"],
                    issuer=ISSUER,
                    organization_id="fuzz-org",
                    email=f"reader-{number}@fuzz.invalid",
                    name=f"Reader {number}",
                )
            )
            token = store_identity(store, user_auth, user_auth.get_settings(), identity)
            readers.append(
                {
                    "identity": identity,
                    "token": token,
                    "headers": {
                        "Cookie": f"__Host-devfeed_user_session={token}",
                        "Origin": READER_ORIGIN,
                        "X-CSRF-Token": identity["csrf_token"],
                    },
                }
            )
        session.add(
            Topic(
                id=topic_id,
                name="Fuzz engineering",
                slug="fuzz-engineering",
                kind="discipline",
                status="active",
            )
        )
        session.add(
            Source(
                id=source_id,
                name="Fuzz source",
                source_type="publisher",
                feed_url="https://source.fuzz.invalid/rss",
                approval_status="approved",
            )
        )
        session.add(Tag(id=tag_id, name="Fuzz tag", slug="fuzz-tag", topic_id=topic_id))
        session.add(
            Article(
                id=article_id,
                title="Fuzz article",
                canonical_url="https://source.fuzz.invalid/article",
                url_hash=article_id.hex,
                publication_status="published",
                review_status="approved",
                language="en",
            )
        )
        session.flush()
        session.add(
            ArticleOrigin(
                article_id=article_id,
                source_id=source_id,
                entry_key="fuzz",
                original_url="https://source.fuzz.invalid/article",
            )
        )
        session.add(ArticleTag(article_id=article_id, tag_id=tag_id))
        session.add(
            ArticleTopic(
                article_id=article_id,
                topic_id=topic_id,
                role="primary",
                relevance=1,
                evidence="Synthetic fuzz fixture",
            )
        )
    admin_identity = {
        "subject": "fuzz-admin",
        "issuer": ISSUER,
        "organization_id": "fuzz-org",
        "roles": ["superuser"],
        "csrf_token": secrets.token_urlsafe(32),
    }
    token = store_identity(store, admin_auth, admin_auth.get_settings(), admin_identity)
    admin = {
        "token": token,
        "identity": admin_identity,
        "headers": {
            "Cookie": f"__Host-devfeed_admin_session={token}",
            "Origin": ADMIN_ORIGIN,
            "X-CSRF-Token": admin_identity["csrf_token"],
        },
    }
    try:
        with ExitStack() as stack:
            apps = {
                service: importlib.import_module(f"{package}.main").create_app()
                for service, package in (
                    ("public", "devfeed_api"),
                    ("user", "devfeed_user_api"),
                    ("admin", "devfeed_admin_api"),
                )
            }
            clients = {
                name: stack.enter_context(
                    TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
                )
                for name, app in apps.items()
            }
            yield SecurityContext(
                apps,
                clients,
                readers,
                admin,
                store,
                database,
                article_id,
                topic_id,
                source_id,
                tag_id,
            )
    finally:
        store.close()
        for module in (admin_auth, user_auth):
            module.get_settings.cache_clear()
