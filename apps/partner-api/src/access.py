"""Live partner account scope and write authorization bound to one request session."""

from __future__ import annotations

import uuid

from devfeed_core.models import PartnerAccount, PartnerMembership, PartnerProduct
from devfeed_http.partner_ad_images import lock_image_account, lock_image_product
from devfeed_http.partner_products import lock_manual_identity
from fastapi import HTTPException
from sqlalchemy import SQLColumnExpression, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.selectable import Select

from devfeed_partner_api.auth import PartnerIdentity


class PartnerAccountAccess:
    def __init__(self, session: Session, identity: PartnerIdentity) -> None:
        self.session = session
        self.identity = identity

    def query(self, account_id: uuid.UUID | None = None) -> Select[PartnerAccount]:
        """Scope all current memberships before pagination; authorize explicit filters."""
        statement = select(PartnerAccount).where(
            PartnerAccount.status == "active",
            self._membership(PartnerAccount.id)
            .with_only_columns(PartnerMembership.account_id)
            .exists(),
        )
        if account_id is not None:
            self.require(account_id)
            statement = statement.where(PartnerAccount.id == account_id)
        return statement

    def require(self, account_id: uuid.UUID) -> PartnerAccount:
        account = self.session.scalar(self.query().where(PartnerAccount.id == account_id))
        if account is None:
            raise HTTPException(404, "Partner account not found")
        return account

    def lock(self, account_id: uuid.UUID, *, nowait: bool = False) -> PartnerAccount:
        """Hold account then membership locks through the caller's commit or rollback."""
        statement = self.query().where(PartnerAccount.id == account_id)
        account = (
            lock_image_account(self.session, statement)
            if nowait
            else self.session.scalar(
                statement.with_for_update().execution_options(populate_existing=True)
            )
        )
        if account is None:
            raise HTTPException(404, "Partner account not found")
        member = self.session.scalar(
            self._membership(account_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        if member is None:
            raise HTTPException(404, "Partner account not found")
        return account

    def _membership(
        self, account_id: uuid.UUID | SQLColumnExpression[uuid.UUID]
    ) -> Select[PartnerMembership]:
        return select(PartnerMembership).where(
            PartnerMembership.account_id == account_id,
            PartnerMembership.issuer == self.identity.issuer,
            PartnerMembership.subject == self.identity.subject,
        )

    def lock_product(self, account_id: uuid.UUID, product_id: uuid.UUID) -> PartnerAccount:
        """Authorize before acquiring product -> account -> membership locks."""
        self.require(account_id)
        lock_image_product(self.session, account_id, product_id)
        return self.lock(account_id, nowait=True)

    def lock_manual(
        self,
        account_id: uuid.UUID,
        *,
        product_id: uuid.UUID | None = None,
        url: str | None = None,
    ) -> tuple[PartnerAccount, PartnerProduct | None]:
        """Keep manual identity's catalog -> product locks ahead of account authorization."""
        self.require(account_id)
        product = lock_manual_identity(self.session, account_id, product_id=product_id, url=url)
        return self.lock(account_id, nowait=True), product
