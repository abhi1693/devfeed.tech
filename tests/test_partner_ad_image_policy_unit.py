"""Account image policies fence leases and preserve storage without database or network services."""

import io
import uuid
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import image_jobs
from devfeed_core import partner_product_images as images
from devfeed_core.config import get_settings
from devfeed_core.managed_images import IMAGE_VERSION, THUMBNAIL_VERSION
from devfeed_core.models import (
    ArticleImageJob,
    PartnerAccount,
    PartnerProduct,
    PartnerProductPromotion,
    utcnow,
)
from devfeed_core.services import RecordNotFound
from devfeed_http import image_uploads, partner_ad_images
from devfeed_http.partner_portal import ProductPromotionInput
from fastapi import HTTPException
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.exc import DBAPIError
from test_avatar_uploads import FakeR2, picture

SOURCE = "https://creative.example/card.png"


@pytest.fixture
def policy_catalog(monkeypatch):
    monkeypatch.setenv("DEVFEED_IMAGE_STORAGE_ENABLED", "true")
    monkeypatch.setenv("DEVFEED_IMAGE_PUBLIC_URL", "https://images.example")
    get_settings.cache_clear()
    account = PartnerAccount(id=uuid.uuid4(), name="Owner", tier="bronze")
    product = PartnerProduct(
        id=uuid.uuid4(), product_url="https://product.example/", managed_image={}
    )
    policy = PartnerProductPromotion(
        account_id=account.id,
        product_id=product.id,
        ad_image_url=SOURCE,
        ad_image_revision=2,
        managed_ad_image={},
        excluded=False,
    )
    return account, product, policy


def uploaded(policy, origin=None):
    origin = origin or policy.product_id
    prefix = images.ad_image_prefix(policy.account_id, origin)
    source = f"https://images.example/{prefix}/800.webp?v=old"
    return {
        "upload": True,
        "object_prefix": prefix,
        "object_product_id": str(origin),
        "cache_version": "old",
        "source_url": source,
        "subject_revision": policy.ad_image_revision,
        "version": IMAGE_VERSION,
        "variants": [{"key": f"{prefix}/800.webp", "width": 800}],
    }


def image_job(policy, **changes):
    return ArticleImageJob(
        **{
            "id": uuid.uuid4(),
            "account_id": policy.account_id,
            "product_id": policy.product_id,
            "operation": "product-ad-image",
            "image_url": policy.ad_image_url,
            "subject_revision": policy.ad_image_revision,
            "storage_version": THUMBNAIL_VERSION,
            "status": "queued",
            "storage": {},
            "attempts": 0,
            "available_at": utcnow() - timedelta(seconds=1),
            **changes,
        }
    )


class ImageSession:
    def __init__(self, product, policy, *, active=None, previous=None, status=None):
        self.product, self.policy = product, policy
        self.active, self.previous, self.status = active, previous, status
        self.statements, self.added = [], []
        self.flushes = 0

    def scalar(self, statement):
        self.statements.append(statement)
        description = statement.column_descriptions[0]
        if description["name"] == "status":
            return self.status
        return {
            PartnerProduct: self.product,
            PartnerProductPromotion: self.policy,
            ArticleImageJob: self.active,
        }.get(description["entity"])

    def get(self, model, identifier):
        return {
            PartnerProduct: self.product,
            PartnerProductPromotion: self.policy,
            ArticleImageJob: self.previous or self.active,
        }[model]

    def add(self, job):
        self.added.append(job)

    def flush(self):
        self.flushes += 1


def test_custom_requests_coalesce_and_replacement_revokes_the_old_lease(policy_catalog):
    account, product, policy = policy_catalog
    active = image_job(policy, status="running", lease_token=uuid.uuid4())
    session = ImageSession(product, policy, active=active)
    assert images.request_product_ad_image(session, account.id, product.id) is active
    assert not session.added and active.status == "running"
    policy.ad_image_revision += 1
    replacement = images.request_product_ad_image(session, account.id, product.id)
    assert replacement is not active and session.added == [replacement]
    assert (active.status, active.outcome, active.lease_token) == (
        "succeeded",
        "already_present",
        None,
    )
    assert replacement.account_id == account.id and replacement.product_id == product.id
    assert replacement.image_url == SOURCE and replacement.subject_revision == 3
    assert replacement.storage_version == THUMBNAIL_VERSION
    assert all(
        "FOR UPDATE" in str(statement.compile(dialect=dialect()))
        for statement in session.statements
    )


@pytest.mark.parametrize("reason", ["clear", "merged", "ready", "disabled", "missing-policy"])
def test_ineligible_or_prepared_custom_images_do_not_schedule_work(policy_catalog, reason):
    account, product, policy = policy_catalog
    active = image_job(policy, subject_revision=1, status="running", lease_token=uuid.uuid4())
    if reason == "clear":
        policy.ad_image_url = None
    elif reason == "merged":
        product.merged_into_id = uuid.uuid4()
    elif reason == "ready":
        policy.managed_ad_image = uploaded(policy)
        policy.ad_image_url = policy.managed_ad_image["source_url"]
    elif reason == "disabled":
        get_settings().image_storage_enabled = False
    session = ImageSession(product, None if reason == "missing-policy" else policy, active=active)
    assert images.request_product_ad_image(session, account.id, product.id) is None
    assert not session.added and active.lease_token is None


def test_missing_product_request_never_touches_other_subjects(policy_catalog):
    account, product, policy = policy_catalog
    session = ImageSession(None, policy)
    with pytest.raises(RecordNotFound):
        images.request_product_ad_image(session, account.id, product.id)
    assert len(session.statements) == 1 and not session.added


@pytest.mark.parametrize(
    "status,expected",
    [
        ("queued", "queued"),
        ("running", "running"),
        ("failed", "failed"),
        ("succeeded", "failed"),
        (None, "failed"),
    ],
)
def test_status_lookup_is_scoped_to_the_current_policy_revision(policy_catalog, status, expected):
    _, product, policy = policy_catalog
    session = ImageSession(product, policy, status=status)
    assert images.product_ad_image_status(session, policy) == expected
    params = session.statements[0].compile(dialect=dialect()).params
    assert policy.account_id in params.values() and product.id in params.values()
    assert policy.ad_image_revision in params.values()
    assert images.product_ad_image_status(session, None) == "default"
    policy.managed_ad_image = uploaded(policy)
    policy.ad_image_url = policy.managed_ad_image["source_url"]
    assert images.product_ad_image_status(session, policy) == "ready"
    assert len(session.statements) == 1
    assert images.product_image_url(product, policy).endswith("/800.webp?v=old")


def test_cleanup_keeps_inherited_origin_and_rejects_forged_namespaces(policy_catalog):
    _, product, policy = policy_catalog
    origin = uuid.uuid4()
    policy.managed_ad_image = uploaded(policy, origin)
    policy.ad_image_url = policy.managed_ad_image["source_url"]
    session = ImageSession(product, policy)
    queued = images.request_ad_image_cleanup(session, policy)
    assert queued.storage == {"cleanup_upload": images.ad_image_prefix(policy.account_id, origin)}
    session.active = queued
    assert images.request_ad_image_cleanup(session, policy) is queued
    assert session.added == [queued]
    for change in (
        {"object_product_id": "invalid"},
        {"object_product_id": None},
        {"object_prefix": "avatars/foreign"},
        {"upload": False},
    ):
        policy.managed_ad_image = {**uploaded(policy, origin), **change}
        assert images.request_ad_image_cleanup(session, policy) is None
    assert images.uploaded_ad_image_prefix({}, policy.account_id) is None


@pytest.mark.parametrize(
    "reason",
    ["current", "cleared", "revision", "source", "merged", "missing-policy", "ready", "disabled"],
)
def test_claim_only_leases_the_current_custom_image(policy_catalog, reason):
    _, product, policy = policy_catalog
    job = image_job(policy)
    if reason == "cleared":
        policy.ad_image_url = None
    elif reason == "revision":
        policy.ad_image_revision += 1
    elif reason == "source":
        policy.ad_image_url = "https://creative.example/new.png"
    elif reason == "merged":
        product.merged_into_id = uuid.uuid4()
    elif reason == "ready":
        policy.managed_ad_image = uploaded(policy)
        policy.ad_image_url = policy.managed_ad_image["source_url"]
    elif reason == "disabled":
        get_settings().image_storage_enabled = False
    session = ImageSession(product, None if reason == "missing-policy" else policy, active=job)
    result = image_jobs.claim_image(session, job.id)
    if reason == "current":
        assert result == (job, SOURCE) and job.status == "running" and job.lease_token
    elif reason == "disabled":
        assert result is None and job.status == "queued" and not job.lease_token
    else:
        assert result is None and job.outcome == "already_present" and not job.lease_token


@pytest.mark.parametrize("valid", [True, False])
def test_cleanup_claim_requires_a_merged_subject_and_valid_captured_prefix(policy_catalog, valid):
    _, product, policy = policy_catalog
    policy.managed_ad_image = uploaded(policy, uuid.uuid4())
    job = image_job(policy, storage={"cleanup_upload": policy.managed_ad_image["object_prefix"]})
    product.merged_into_id = uuid.uuid4() if valid else None
    session = ImageSession(product, policy, active=job)
    result = image_jobs.claim_image(session, job.id)
    assert bool(result) == valid
    assert (job.status == "running") == valid


@pytest.mark.parametrize("same_revision", [True, False])
def test_retry_reuses_checkpoints_only_for_the_same_source_generation(
    policy_catalog, same_revision
):
    _, product, policy = policy_catalog
    previous = image_job(
        policy, status="failed", storage={"variants": [{"width": 400, "key": "safe"}]}
    )
    if not same_revision:
        policy.ad_image_revision += 1
    session = ImageSession(product, policy, previous=previous)
    retry = image_jobs.retry_image(session, previous.id)
    assert retry.subject_revision == policy.ad_image_revision
    assert bool(retry.storage) == same_revision
    if same_revision:
        assert retry.storage == previous.storage and retry.storage is not previous.storage


@pytest.mark.parametrize("outcome", [0, 17, "unavailable"])
def test_private_upload_budgets_are_actor_and_account_scoped(policy_catalog, monkeypatch, outcome):
    account, _, _ = policy_catalog
    captured = []

    def charge(redis, budgets):
        captured.extend(budgets)
        if outcome == "unavailable":
            raise RedisError("private connection")
        return outcome

    monkeypatch.setattr(partner_ad_images, "consume_rate_limits", charge)
    actor = SimpleNamespace(issuer="https://identity.example", subject="reader-private")
    if outcome:
        with pytest.raises(HTTPException) as error:
            partner_ad_images.limit_ad_image_upload(object(), actor, account.id)
        assert error.value.status_code == (503 if outcome == "unavailable" else 429)
        if outcome == 17:
            assert error.value.headers["Retry-After"] == "17"
    else:
        partner_ad_images.limit_ad_image_upload(object(), actor, account.id)
    assert [(budget.limit, budget.window) for budget in captured] == [(10, 60), (30, 3600)]
    assert str(account.id) in captured[1].key and actor.subject not in captured[0].key


@pytest.mark.parametrize("sqlstate", ["55P03", "40P01"])
def test_busy_account_rollback_releases_product_locks_only_for_retryable_contention(sqlstate):
    original = RuntimeError("database unavailable")
    original.sqlstate = sqlstate
    failure = DBAPIError("query", {}, original)
    session = Mock()
    session.scalar.side_effect = failure
    if sqlstate == "55P03":
        with pytest.raises(HTTPException) as error:
            partner_ad_images.lock_image_account(session, select(PartnerAccount))
        assert error.value.status_code == 503 and error.value.headers["Retry-After"] == "1"
        session.rollback.assert_called_once()
    else:
        with pytest.raises(DBAPIError):
            partner_ad_images.lock_image_account(session, select(PartnerAccount))
        session.rollback.assert_not_called()


@pytest.mark.parametrize("owned", [True, False])
def test_foreign_products_are_rejected_before_locking(policy_catalog, owned):
    account, product, _ = policy_catalog
    session = Mock()
    session.execute.return_value.first.return_value = product if owned else None
    session.scalar.return_value = product
    if owned:
        partner_ad_images.lock_image_product(session, account.id, product.id)
        assert "FOR UPDATE" in str(session.scalar.call_args.args[0].compile(dialect=dialect()))
    else:
        with pytest.raises(HTTPException) as error:
            partner_ad_images.lock_image_product(session, account.id, product.id)
        assert error.value.status_code == 404
        session.scalar.assert_not_called()


class PolicyTransaction:
    def __init__(self, policy, *, fail=False):
        self.policy, self.fail = policy, fail
        self.committed = None

    def execute(self, statement):
        params = statement.compile(dialect=dialect()).params
        for field in ("ad_image_url", "managed_ad_image", "ad_image_revision"):
            setattr(self.policy, field, params[field])

    def commit(self):
        if self.fail:
            raise RuntimeError("commit failed")
        self.committed = deepcopy(self.policy.managed_ad_image)


@pytest.fixture
def upload_policy(policy_catalog, monkeypatch):
    account, product, policy = policy_catalog
    r2 = FakeR2()
    monkeypatch.setattr(image_uploads, "image_upload_client", lambda: r2)
    monkeypatch.setattr(
        partner_ad_images, "_product_promotion_policy", lambda *args, **kwargs: policy
    )
    monkeypatch.setattr(
        partner_ad_images,
        "product_promotion",
        lambda *args: images.product_image_url(product, policy),
    )
    fence = Mock()
    monkeypatch.setattr(partner_ad_images, "request_product_ad_image", fence)
    return account, product, policy, r2, fence


@pytest.mark.parametrize("fail", [False, True])
def test_upload_transfers_inherited_storage_with_transactional_rollback(upload_policy, fail):
    account, product, policy, r2, fence = upload_policy
    policy.managed_ad_image = uploaded(policy, uuid.uuid4())
    policy.ad_image_url = policy.managed_ad_image["source_url"]
    old_prefix = policy.managed_ad_image["object_prefix"]
    r2.objects = {f"{old_prefix}/{size}.webp": b"previous" for size in images.AD_IMAGE_SIZES}
    previous = dict(r2.objects)
    transaction = PolicyTransaction(policy, fail=fail)
    file = SimpleNamespace(content_type="image/png", file=io.BytesIO(picture(size=(120, 80))))
    if fail:
        with pytest.raises(RuntimeError, match="commit failed"):
            partner_ad_images.upload_product_ad_image(transaction, account, product.id, file)
        assert r2.objects == previous and transaction.committed is None
    else:
        result = partner_ad_images.upload_product_ad_image(transaction, account, product.id, file)
        assert result == policy.ad_image_url and "?v=" in result
        assert len(r2.objects) == 3 and not set(previous) & set(r2.objects)
        assert [item["width"] for item in policy.managed_ad_image["variants"]] == [120]
        assert policy.ad_image_revision == 3 and transaction.committed
    fence.assert_called_once_with(transaction, account.id, product.id)


@pytest.mark.parametrize("invalid", ["mime", "origin"])
def test_invalid_upload_does_not_allocate_storage(upload_policy, monkeypatch, invalid):
    account, product, policy, r2, _ = upload_policy
    file = SimpleNamespace(
        content_type="image/svg+xml" if invalid == "mime" else "image/png",
        file=io.BytesIO(b"not decoded"),
    )
    if invalid == "origin":
        get_settings().image_public_url = "http://images.example"
    with pytest.raises(HTTPException) as error:
        partner_ad_images.upload_product_ad_image(
            PolicyTransaction(policy), account, product.id, file
        )
    assert error.value.status_code == (415 if invalid == "mime" else 503)
    assert not r2.objects and not r2.puts


@pytest.mark.parametrize("source", [None, SOURCE, "same-directory"])
@pytest.mark.parametrize("fail", [False, True])
def test_policy_save_removes_uploads_only_after_safe_source_choice(
    upload_policy, monkeypatch, source, fail
):
    account, product, policy, r2, _ = upload_policy
    policy.managed_ad_image = uploaded(policy)
    policy.ad_image_url = policy.managed_ad_image["source_url"]
    prefix = policy.managed_ad_image["object_prefix"]
    r2.objects = {f"{prefix}/{size}.webp": b"previous" for size in images.AD_IMAGE_SIZES}
    previous = dict(r2.objects)
    if source == "same-directory":
        source = policy.ad_image_url.replace("?v=old", "?v=edited")
    payload = ProductPromotionInput(ad_image_url=source)
    transaction = PolicyTransaction(policy, fail=fail)
    save = Mock(return_value="saved")
    monkeypatch.setattr(partner_ad_images, "save_product_promotion", save)
    if source and source.startswith("https://images.example"):
        with pytest.raises(HTTPException) as error:
            partner_ad_images.save_product_promotion_with_images(
                transaction, account, product.id, payload
            )
        assert error.value.status_code == 422 and r2.objects == previous
        save.assert_not_called()
    elif fail:
        with pytest.raises(RuntimeError):
            partner_ad_images.save_product_promotion_with_images(
                transaction, account, product.id, payload
            )
        assert r2.objects == previous
    else:
        assert (
            partner_ad_images.save_product_promotion_with_images(
                transaction, account, product.id, payload
            )
            == "saved"
        )
        assert not r2.objects
