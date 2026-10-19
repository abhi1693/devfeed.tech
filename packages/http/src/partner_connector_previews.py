"""Bounded connector previews shared by the independently authorized private APIs."""

import logging
import time

from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.partner_connections import ConnectorPreviewOut, ConnectorResponseOut
from devfeed_core.partner_connectors import ConnectorClient, ConnectorConfig, ConnectorDiagnostic
from fastapi import HTTPException
from pydantic import ValidationError

logger = logging.getLogger(__name__)


def public_diagnostic(error: Exception, fallback: str) -> str:
    """Expose only deliberately public messages and structured error metadata."""
    if isinstance(error, ConnectorDiagnostic):
        return str(error)
    if isinstance(error, FeedError):
        if error.status in {401, 403}:
            return (
                f"The import API rejected authentication (HTTP {error.status}). "
                "Check the credential and its permissions."
            )
        if isinstance(error.status, int) and 400 <= error.status <= 599:
            return (
                f"The import API returned HTTP {error.status}. "
                "Check the endpoint and API availability."
            )
        if error.reason == "transport_error":
            return (
                "Could not connect to the import API or the request timed out. "
                "Check API availability."
            )
    if isinstance(error, ValidationError):
        labels = {
            "name": "Product name",
            "external_id": "Product ID",
            "tagline": "Tagline",
            "product_url": "Website URL",
            "listing_url": "Listing URL",
        }
        fields = list(
            dict.fromkeys(
                labels[issue["loc"][0]]
                for issue in error.errors()
                if issue["loc"] and issue["loc"][0] in labels
            )
        )
        if fields:
            return (
                ", ".join(fields)
                + ": mapped value is invalid. Check the field paths and value format."
            )
    return fallback


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
                public_diagnostic(
                    exc,
                    "Could not read the product page. "
                    "Check endpoints, response paths and credentials.",
                ),
            ) from None
        products, errors = [], []
        for index, candidate in enumerate(candidates[:3], start=1):
            try:
                products.append(self.client.product(candidate))
            except Exception as exc:
                message = public_diagnostic(
                    exc,
                    "A product could not be mapped. Check field paths, detail endpoint and URLs.",
                )
                errors.append(f"Product {index}: {message}")
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
                422,
                public_diagnostic(
                    exc, "Could not read the API response. Check the endpoint and authentication."
                ),
            ) from None

    def _log_failure(self, event, error):
        logger.info(
            event,
            extra={"provider": self.client.provider, "error_type": type(error).__name__},
        )
