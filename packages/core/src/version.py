"""Installed release identity, independent of runtime settings and connections."""

from importlib.metadata import version

__version__ = version("devfeed-core")

# Schema revisions are not application versions. Advance this when adding a
# migration required by this application; never change an existing migration.
SCHEMA_REVISION = "0006"

# 0006 adds indexes only. Accept the preceding schema while new pods roll out,
# then run the concurrent migration after all old (0005-only) API pods retire.
BACKWARD_COMPATIBLE_SCHEMA_REVISIONS = frozenset({"0005"})
