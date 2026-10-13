"""Bounded connector previews shared by the independently authorized private APIs."""

import logging
import time

from devfeed_core.partner_connections import ConnectorPreviewOut, ConnectorResponseOut
from devfeed_core.partner_connectors import ConnectorClient, ConnectorConfig
from fastapi import HTTPException

logger = logging.getLogger(__name__)


class ConnectorPreviewService:
    """Read one page and at most three products without persisting import state."""

    def __init__(self, provider: str, config: ConnectorConfig, *, credential: str | None = None):
        self.client = ConnectorClient(
            provider,
            config.model_copy(update={"timeout_seconds": min(config.timeout_seconds, 5)}),
            credential=credential,
            deadline=time.monotonic() + 20,
        )

    def products(self) -> ConnectorPreviewOut:
        try:
            candidates, cursor = self.client.discover_page()
        except Exception as exc:
            self._log_failure("partner_connector_preview_failed", exc)
            raise HTTPException(
                422,
                "Could not read the product page. Check endpoints, response paths and credentials.",
            ) from None
        products, errors = [], []
        for candidate in candidates[:3]:
            try:
                products.append(self.client.product(candidate))
            except Exception:
                errors.append(
                    "A product could not be mapped. Check field paths, detail endpoint and URLs."
                )
        return ConnectorPreviewOut(
            products=products,
            errors=errors,
            has_next_page=bool(cursor),
            discovered=len(candidates),
        )

    def response(self) -> ConnectorResponseOut:
        try:
            return ConnectorResponseOut.model_validate(self.client.response_preview())
        except Exception as exc:
            self._log_failure("partner_connector_response_failed", exc)
            raise HTTPException(
                422, "Could not read the API response. Check the endpoint and authentication."
            ) from None

    def _log_failure(self, event, error):
        logger.info(
            event,
            extra={"provider": self.client.provider, "error_type": type(error).__name__},
        )
