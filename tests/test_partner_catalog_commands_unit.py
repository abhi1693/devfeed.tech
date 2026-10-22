"""Canonical identity commands consume scoped reads, without simulating SQL."""

import copy
import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_core import partner_catalog as catalog
from devfeed_core import (
    partner_connections,
    partner_product_images,
    partner_promotions,
    partner_tools,
)
from devfeed_core.models import (
    ArticleImageJob,
    PartnerAccount,
    PartnerListing,
    PartnerManualProduct,
    PartnerProduct,
    PartnerProductPromotion,
    PartnerProductURL,
)
from devfeed_core.partner_tools import ListingOut, ProductInput
from devfeed_core.urls import fingerprint
from sqlalchemy.orm import Session
from test_partner_connection_workflow_unit import connection, work
from test_partner_matching_workflow_unit import NOW, product, result
from test_partner_product_ad_images import managed


def listing(value, **changes):
    return PartnerListing(
        **{
            "id": uuid.uuid4(),
            "product_id": value.id,
            "provider": "nick-launches",
            "external_id": "api-checker",
            "name": value.name,
            "product_url": value.product_url,
            "listing_url": "https://nicklaunches.com/products/checker/",
            "tagline": value.tagline,
            "pricing": value.pricing,
            "attribution": "Via launch platform",
            "active": True,
            "identity_status": "resolved",
            "updated_at": NOW - timedelta(days=1),
            **changes,
        }
    )


def policy(account, value, **changes):
    return PartnerProductPromotion(
        **{
            "account_id": account.id,
            "product_id": value.id,
            "excluded": False,
            "ad_formats": None,
            "promotion_surfaces": None,
            "link_rel": None,
            "ad_image_url": None,
            "ad_image_revision": 0,
            "managed_ad_image": {},
            **changes,
        }
    )


@pytest.fixture
def commands(monkeypatch):
    monkeypatch.setattr(catalog, "utcnow", lambda: NOW)
    monkeypatch.setattr(partner_tools, "utcnow", lambda: NOW)
    assessment, evaluation, enrollment = Mock(), Mock(), Mock()
    monkeypatch.setattr(partner_connections, "request_assessment", assessment)
    monkeypatch.setattr(partner_connections, "queue_evaluation", evaluation)
    monkeypatch.setattr(partner_promotions, "ensure_automatic_assets", enrollment)
    return assessment, evaluation, enrollment


@pytest.mark.parametrize(
    "case",
    ["unchanged", "metadata", "secondary", "manual_authority", "unresolved", "excluded", "expired"],
)
def test_import_preserves_authority_and_requalifies_only_changed_or_expired_canonical_facts(
    commands, case
):
    assessment, evaluation, enrollment = commands
    value = product()
    source = listing(value)
    value.metadata_listing_id = source.id
    if case == "secondary":
        value.metadata_listing_id = uuid.uuid4()
    if case == "manual_authority":
        value.metadata_listing_id, value.metadata_account_id = None, uuid.uuid4()
    if case == "excluded":
        value.status, value.excluded = "paused", True
    if case == "expired":
        value.verified_at = NOW - timedelta(days=91)
    payload = ProductInput(
        **{
            name: getattr(source, name)
            for name in ProductInput.model_fields
            if hasattr(source, name)
        }
    )
    if case in {"metadata", "secondary", "manual_authority"}:
        payload = payload.model_copy(update={"name": "API Checker Pro"})
    if case == "unresolved":
        payload = payload.model_copy(update={"product_url": "https://changed.example/"})
    alias = (
        None
        if case == "unresolved"
        else PartnerProductURL(
            url_hash=fingerprint(payload.product_url), url=payload.product_url, product_id=value.id
        )
    )
    session = Mock(spec=Session)
    session.get.side_effect = lambda model, _key: alias if model is PartnerProductURL else value
    session.scalar.side_effect = [source, source.id, value.id]
    old_updated, old_evidence = value.updated_at, copy.deepcopy(value.evidence)
    generation = uuid.uuid4()

    assert catalog.upsert_listing(session, payload, generation) is value

    assert source.seen_generation == generation and source.updated_at == NOW and source.active
    assert source.name == payload.name and source.product_url == payload.product_url
    assert source.identity_status == ("unresolved" if case == "unresolved" else "resolved")
    if case == "metadata":
        assert value.name == "API Checker Pro" and value.revision == 3
        assert value.verified_at is None and value.evidence == [] and value.technologies == []
        assert value.updated_at == NOW and value.assessment == {}
    else:
        assert value.name == "API Checker" and value.revision == 2
        assert value.updated_at == old_updated and value.evidence == old_evidence
    if case in {"metadata", "expired"}:
        assessment.assert_called_once_with(session, value)
    else:
        assessment.assert_not_called()
    evaluation.assert_called_once_with(session, value)
    enrollment.assert_called_once_with(session, product_ids=[value.id])


def test_known_alias_adds_platform_provenance_without_replacing_metadata(commands):
    assessment, _, _ = commands
    value = product(metadata_listing_id=uuid.uuid4())
    incoming = ProductInput(
        provider="shipyardhq",
        external_id="checker",
        name="Platform display name",
        product_url=value.product_url,
        listing_url="https://shipyardhq.example/products/checker",
        tagline="A launch description from a second platform.",
    )
    alias = PartnerProductURL(
        url_hash=fingerprint(value.product_url), url=value.product_url, product_id=value.id
    )
    session = Mock(spec=Session)
    session.get.side_effect = lambda model, _key: alias if model is PartnerProductURL else value
    session.scalar.side_effect = [None, uuid.uuid4(), value.id]

    def generated_identity():
        for call in session.add.call_args_list:
            if isinstance(call.args[0], PartnerListing) and call.args[0].id is None:
                call.args[0].id = uuid.uuid4()

    session.flush.side_effect = generated_identity
    assert catalog.upsert_listing(session, incoming, uuid.uuid4()) is value
    created = session.add.call_args.args[0]
    assert isinstance(created, PartnerListing) and created.product_id == value.id
    assert created.provider == "shipyardhq" and created.identity_status == "resolved"
    assert value.name == "API Checker" and value.revision == 2
    assessment.assert_not_called()


def test_new_import_discards_unverified_platform_capability_claims(commands):
    assessment, _, _ = commands
    incoming = ProductInput(
        provider="nick-launches",
        external_id="new-checker",
        name="API Checker",
        product_url="https://new-checker.example/",
        listing_url="https://nicklaunches.com/products/new-checker",
        tagline="Detect incompatible API changes in CI.",
        technologies=["OpenAPI"],
        evidence=[
            {
                "url": "https://new-checker.example/docs",
                "quote": "Detect breaking OpenAPI changes before deployment.",
                "capability": "Detect incompatible API changes.",
            }
        ],
    )
    session = Mock(spec=Session)
    session.get.return_value = None
    session.scalar.side_effect = [None, uuid.uuid4(), uuid.uuid4()]

    def generated_defaults():
        # Only return generated identities/defaults at the unit-of-work boundary;
        # no uniqueness, selection or persistence behavior is implemented.
        for call in session.add.call_args_list:
            row = call.args[0]
            if isinstance(row, (PartnerProduct, PartnerListing)) and row.id is None:
                row.id = uuid.uuid4()
            if isinstance(row, PartnerProduct) and row.revision is None:
                row.revision, row.assessment_revision = 1, 0
                row.status, row.excluded, row.assessment = "pending", False, {}

    session.flush.side_effect = generated_defaults
    saved = catalog.upsert_listing(session, incoming, uuid.uuid4())
    assert saved.technologies == [] and saved.evidence == [] and saved.status == "pending"
    assert saved.verified_at is None and saved.metadata_listing_id
    alias = next(
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], PartnerProductURL)
    )
    assert alias.product_id == saved.id and alias.url == incoming.product_url
    assessment.assert_called_once_with(session, saved)


def test_withdrawal_refreshes_each_affected_identity_once_and_counts_listing_rows(monkeypatch):
    first, second = product(), product()
    missing = [listing(first), listing(first, external_id="checker-duplicate"), listing(second)]
    session = Mock(spec=Session)
    session.scalars.return_value = result(missing)
    session.get.side_effect = lambda _model, key: {first.id: first, second.id: second}[key]
    refreshed = Mock()
    monkeypatch.setattr(catalog, "refresh_availability", refreshed)
    monkeypatch.setattr(catalog, "utcnow", lambda: NOW)
    assert catalog.withdraw_missing_listings(session, "nick-launches", uuid.uuid4(), limit=3) == 3
    assert all(not row.active and row.updated_at == NOW for row in missing)
    assert {call.args[1].id for call in refreshed.call_args_list} == {first.id, second.id}
    assert refreshed.call_count == 2


@pytest.mark.parametrize(
    "case", ["qualified", "trusted", "excluded", "paused", "unresolved", "disabled", "unowned"]
)
def test_listing_views_keep_commercial_acceptance_separate_from_factual_qualification(
    commands, case
):
    value = product()
    account = PartnerAccount(
        id=uuid.uuid4(),
        name="Launch platform",
        status="active",
        tier="silver",
        approval_mode="trusted" if case == "trusted" else "qualified",
    )
    acceptance = policy(account, value)
    if case == "trusted":
        value.status, value.verified_at = "pending", None
        acceptance.commercial_approved_at, acceptance.approval_source = NOW, "trusted"
    if case == "excluded":
        acceptance.excluded = True
    if case == "paused":
        account.status = "paused"
    source = listing(
        value,
        identity_status="unresolved" if case == "unresolved" else "resolved",
        identity_reason="Waiting for verified alias" if case == "unresolved" else None,
    )
    session = Mock(spec=Session)
    session.execute.return_value = [
        (
            source,
            case != "disabled",
            "",
            None if case == "unowned" else account,
            None if case == "unowned" else acceptance,
            value,
        )
    ]
    validated = ListingOut.model_validate(catalog.listing_views(session, [value.id])[value.id][0])
    assert validated.commercial_approved is (case in {"qualified", "trusted"})
    assert validated.platform_name == "Nick Launches"
    if case == "trusted":
        assert validated.approval_source == "trusted" and value.verified_at is None
    assert source.identity_status == ("unresolved" if case == "unresolved" else "resolved")


def test_empty_listing_page_needs_no_repository_query():
    session = Mock(spec=Session)
    assert catalog.listing_views(session, []) == {}
    session.execute.assert_not_called()


@pytest.mark.parametrize("image_choice", ["inherit", "keep", "cleared"])
def test_merge_preserves_restrictive_account_policy_and_explicit_creative_choice(
    commands, monkeypatch, image_choice
):
    assessment, _, _ = commands
    donor, recipient = product(product_url="http://www.checker.example/"), product()
    account = PartnerAccount(
        id=uuid.uuid4(),
        name="Launch platform",
        status="active",
        tier="platinum",
        link_rel=["nofollow"],
    )
    incoming = policy(
        account,
        donor,
        excluded=True,
        ad_formats=["feed", "engagement"],
        promotion_surfaces=["article"],
        link_rel=["ugc"],
        ad_image_url="https://creative.example/donor.png",
        ad_image_revision=2,
        managed_ad_image=managed("https://creative.example/donor.png", revision=2),
        commercial_approved_at=NOW - timedelta(days=1),
        approval_source="trusted",
    )
    current = policy(
        account,
        recipient,
        ad_formats=["feed"],
        promotion_surfaces=None,
        link_rel=["nofollow"],
        ad_image_revision=0 if image_choice == "inherit" else 3,
        ad_image_url="https://creative.example/current.png" if image_choice == "keep" else None,
    )
    old_image = copy.deepcopy(incoming.managed_ad_image)
    if image_choice != "inherit":
        prefix = partner_product_images.ad_image_prefix(account.id, donor.id)
        incoming.ad_image_url = f"https://images.example/{prefix}/800.webp?v=uploaded-v2"
        incoming.managed_ad_image = {
            "upload": True,
            "object_prefix": prefix,
            "object_product_id": str(donor.id),
            "cache_version": "uploaded-v2",
            "source_url": incoming.ad_image_url,
            "subject_revision": 2,
            "version": partner_product_images.IMAGE_VERSION,
            "variants": [
                {"key": f"{prefix}/{width}.webp", "width": width}
                for width in partner_product_images.AD_IMAGE_SIZES
            ],
        }
    source = PartnerManualProduct(
        account_id=account.id,
        product_id=donor.id,
        active=True,
        created_at=NOW - timedelta(days=2),
        updated_at=NOW - timedelta(days=2),
    )
    existing = PartnerManualProduct(
        account_id=account.id,
        product_id=recipient.id,
        active=False,
        created_at=NOW - timedelta(days=3),
        updated_at=NOW - timedelta(days=1),
    )
    importing = work(connection(), operation="assess", product_id=donor.id, status="running")
    image_job = ArticleImageJob(
        id=uuid.uuid4(),
        product_id=donor.id,
        account_id=account.id,
        operation="product-ad-image",
        status="queued",
        attempts=0,
        created_at=NOW - timedelta(minutes=1),
        available_at=NOW - timedelta(minutes=1),
    )
    session = Mock(spec=Session)
    session.scalars.side_effect = [[importing], [], [source], [incoming], [image_job]]
    session.scalar.side_effect = [account, uuid.uuid4()]
    session.get.side_effect = lambda model, _key: (
        existing if model is PartnerManualProduct else current
    )
    prepare, cleanup = Mock(), Mock()
    monkeypatch.setattr(partner_product_images, "request_product_ad_image", prepare)
    monkeypatch.setattr(partner_product_images, "request_ad_image_cleanup", cleanup)
    assert catalog.merge_products(session, donor, recipient) is recipient
    assert donor.status == "withdrawn" and donor.merged_into_id == recipient.id
    assert importing.status == "failed" and importing.lease_token is None
    assert not source.active and not existing.active
    assert (
        current.excluded
        and current.ad_formats == ["feed"]
        and current.promotion_surfaces == ["article"]
    )
    assert current.link_rel == ["nofollow", "ugc"]
    assert current.commercial_approved_at == incoming.commercial_approved_at
    assert current.approval_source == "trusted"
    assert image_job.status == "succeeded" and image_job.outcome == "already_present"
    if image_choice == "inherit":
        assert current.ad_image_url == incoming.ad_image_url and current.ad_image_revision == 2
        assert current.managed_ad_image == {**old_image, "subject_revision": 2}
        prepare.assert_called_once_with(session, account.id, recipient.id)
        cleanup.assert_not_called()
    else:
        assert current.ad_image_revision == 3
        assert current.ad_image_url == (
            "https://creative.example/current.png" if image_choice == "keep" else None
        )
        prepare.assert_not_called()
        cleanup.assert_called_once_with(session, incoming)
    assessment.assert_not_called()


def test_merge_adds_missing_manual_ownership_and_inherits_empty_allowlist(commands):
    donor, recipient = product(product_url="http://www.checker.example/"), product()
    account = PartnerAccount(
        id=uuid.uuid4(),
        name="Launch platform",
        status="active",
        tier="silver",
        link_rel=["nofollow"],
    )
    source = PartnerManualProduct(
        account_id=account.id,
        product_id=donor.id,
        active=True,
        created_at=NOW - timedelta(days=2),
        updated_at=NOW - timedelta(days=1),
    )
    incoming = policy(account, donor, ad_formats=[], promotion_surfaces=[])
    session = Mock(spec=Session)
    session.scalars.side_effect = [[], [], [source], [incoming], []]
    session.scalar.side_effect = [account, uuid.uuid4()]
    session.get.return_value = None
    assert catalog.merge_products(session, donor, recipient) is recipient
    saved = [call.args[0] for call in session.add.call_args_list]
    ownership = next(row for row in saved if isinstance(row, PartnerManualProduct))
    acceptance = next(row for row in saved if isinstance(row, PartnerProductPromotion))
    assert ownership.active and ownership.product_id == recipient.id
    assert ownership.created_at == source.created_at and not source.active
    assert acceptance.ad_formats == [] and acceptance.promotion_surfaces == []


def test_self_merge_has_no_effect_or_repository_reads():
    value, session = product(), Mock(spec=Session)
    assert catalog.merge_products(session, value, value) is value
    assert session.mock_calls == []
