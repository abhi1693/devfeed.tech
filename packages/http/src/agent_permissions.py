"""Agent permissions belong to the endpoint selected by framework routing."""

from typing import Any, Literal

from fastapi import Request
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, ValidationError

AGENT_POLICY_EXTENSION = "x-devfeed-agent"


class AgentAccessPolicy(BaseModel):
    """Explicit access for agent identities, independent of browser sessions."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    scope: Literal["devfeed:read", "devfeed:write"]

    def openapi_extra(self) -> dict[str, Any]:
        return {AGENT_POLICY_EXTENSION: self.model_dump()}

    @classmethod
    def from_request(cls, request: Request) -> "AgentAccessPolicy | None":
        """Resolve the selected operation; absent or invalid metadata denies access."""
        route = request.scope.get("route")
        if not isinstance(route, APIRoute):
            return None
        extra = route.openapi_extra or {}
        if AGENT_POLICY_EXTENSION not in extra:
            return None
        try:
            return cls.model_validate(extra[AGENT_POLICY_EXTENSION])
        except ValidationError:
            return None
