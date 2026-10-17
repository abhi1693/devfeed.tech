"""The supported function scope releases each API's session before response I/O."""

import importlib
from unittest.mock import Mock

import pytest
from devfeed_core import db
from fastapi import BackgroundTasks, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, field_serializer


@pytest.mark.parametrize("service", ["devfeed_api", "devfeed_admin_api", "devfeed_user_api"])
@pytest.mark.parametrize("outcome", ["success", "handler_error", "serialization_error"])
def test_database_dependency_closes_after_serialization_before_response(
    monkeypatch, service, outcome
):
    dependencies = importlib.import_module(f"{service}.dependencies")
    events = []

    class TrackedSession:
        closed = False

        def __enter__(self):
            events.append("open")
            return self

        def __exit__(self, *_args):
            self.closed = True
            events.append("close")

    session = TrackedSession()
    monkeypatch.setattr(db, "get_engine", Mock())
    monkeypatch.setattr(db, "sessionmaker", lambda *_args, **_kwargs: lambda: session)

    class Payload(BaseModel):
        value: str

        @field_serializer("value")
        def serialize_value(self, value):
            assert not session.closed
            events.append("serialize")
            if outcome == "serialization_error":
                raise RuntimeError("Serialization failed")
            return value

    app = FastAPI()

    def background_work():
        assert session.closed
        events.append("background")

    @app.get("/probe", response_model=Payload)
    def probe(background: BackgroundTasks, value: dependencies.DB):
        assert value is session and not session.closed
        events.append("handler")
        if outcome == "handler_error":
            raise RuntimeError("Handler failed")
        background.add_task(background_work)
        return Payload(value="ready")

    async def observe_response(scope, receive, send):
        async def observe_send(message):
            if message["type"].startswith("http.response."):
                assert session.closed
                events.append(message["type"])
            await send(message)

        await app(scope, receive, observe_send)

    with TestClient(observe_response, raise_server_exceptions=False) as client:
        response = client.get("/probe")
    assert response.status_code == (200 if outcome == "success" else 500)
    assert events[:2] == ["open", "handler"]
    assert events.count("close") == 1
    assert events.index("close") < events.index("http.response.start")
    if outcome == "success":
        assert response.json() == {"value": "ready"}
        assert events.index("serialize") < events.index("close")
        assert events[-1] == "background"
    elif outcome == "serialization_error":
        assert events.index("serialize") < events.index("close")
