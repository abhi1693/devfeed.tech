"""Source semantics, independent of the RSS/Atom format or website hostname."""

from enum import StrEnum


class SourceType(StrEnum):
    PUBLISHER = "publisher"
    AGGREGATOR = "aggregator"
