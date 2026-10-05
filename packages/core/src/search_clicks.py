"""Short-lived receipts binding analytics writes to an actual public search hit."""

import hashlib
import hmac
import re
import time
import uuid
from collections.abc import Callable

from devfeed_core.search_suggestions import normalize_query, query_hash

CLICK_TOKEN_TTL = 7200  # Longer than the maximum public response-cache TTL (one hour).
_TOKEN = re.compile(r"([0-9]{1,12})\.([0-9a-f]{64})", re.ASCII)


class SearchClickTokens:
    def __init__(self, key: str, *, clock: Callable[[], float] = time.time):
        # The Typesense search key is server-only. Derive a purpose-specific key;
        # neither the original key nor the query text appears in a receipt.
        self.key = hmac.digest(key.encode("utf-8"), b"devfeed:search-click:v1", "sha256")
        self.clock = clock

    def _signature(self, query: str, kind: str, result_id: uuid.UUID, expires: int) -> str:
        normalized = normalize_query(query, casefold=False)
        payload = f"v1:{expires}:{kind}:{result_id}:{query_hash(normalized)}".encode("ascii")
        return hmac.new(self.key, payload, hashlib.sha256).hexdigest()

    def issue(self, query: str, kind: str, result_id: uuid.UUID) -> str:
        expires = int(self.clock()) + CLICK_TOKEN_TTL
        return f"{expires}.{self._signature(query, kind, result_id, expires)}"

    def valid(self, token: str, query: str, kind: str, result_id: uuid.UUID) -> bool:
        match = _TOKEN.fullmatch(token)
        if not match:
            return False
        expires = int(match[1])
        now = int(self.clock())
        if expires <= now or expires > now + CLICK_TOKEN_TTL + 60:
            return False
        try:
            expected = self._signature(query, kind, result_id, expires)
        except ValueError:
            return False
        return hmac.compare_digest(expected, match[2])
