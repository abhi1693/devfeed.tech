"""Installed release identity, independent of runtime settings and connections."""

from importlib.metadata import version

__version__ = version("devfeed-core")

# Schema revisions are not application versions. Advance this when adding a
# migration required by this application; never change an existing migration.
SCHEMA_REVISION = "0027"

# Scheduled like-based recommendations require migration 0015.
# The lifecycle migration adds indexes only; new processes can start on 0026
# before the indexes are applied. Roll out those processes before migrating.
BACKWARD_COMPATIBLE_SCHEMA_REVISIONS: frozenset[str] = frozenset({"0026"})
