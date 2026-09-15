"""Installed release identity, independent of runtime settings and connections."""

from importlib.metadata import version

__version__ = version("devfeed-core")

# Schema revisions are not application versions. Advance this when adding a
# migration required by this application; never change an existing migration.
SCHEMA_REVISION = "0013"

# Managed article images require migration 0013.
BACKWARD_COMPATIBLE_SCHEMA_REVISIONS: frozenset[str] = frozenset()
