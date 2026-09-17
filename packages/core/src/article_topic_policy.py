"""Reversible pause for article-derived taxonomy proposals, independent of full automation."""

from sqlalchemy import true

from devfeed_core.config import get_settings
from devfeed_core.models import TopicProposal

ARTICLE_ORIGINS = ("article_enrichment", "ai_analysis")


def proposal_allowed(proposal: TopicProposal) -> bool:
    return get_settings().article_topic_proposals_enabled or proposal.origin not in ARTICLE_ORIGINS


def proposal_condition():
    return (
        true()
        if get_settings().article_topic_proposals_enabled
        else TopicProposal.origin.not_in(ARTICLE_ORIGINS)
    )
