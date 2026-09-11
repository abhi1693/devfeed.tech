"""Session initialization must preserve query limits through session poolers."""

import pytest
from devfeed_core.db import get_engine
from sqlalchemy import text


@pytest.mark.integration
def test_statement_timeout_survives_rollback(database):
    with get_engine().connect() as connection:
        assert connection.scalar(text("SHOW statement_timeout")) == "30s"
        connection.rollback()
        assert connection.scalar(text("SHOW statement_timeout")) == "30s"
