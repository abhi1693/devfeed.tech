"""Shared editorial scope for topic research and independent approval review."""

from devfeed_core.config import get_settings

SCOPE_POLICY = """DevFeed is a feed for software developers. A topic must have a direct,
substantive connection to programming, software engineering, developer tools,
computing infrastructure, security, or the technical foundations of computing.
Assess the exact proposed entity, not a related entity or a hypothetical use case.
General entertainment, games/franchises, hobbies, sports, celebrities and consumer
products are out of scope unless primary evidence establishes that this exact
entity is itself a developer platform/tool or a substantive computing subject.
A game engine, modding API or game-development technique can qualify; a trading
card franchise such as Yu-Gi-Oh! does not qualify merely because it has video games.
Having a website, using software, appearing on GitHub, having fan projects, or
being imported from a topic directory does not establish developer relevance.
Do not invent a programming connection to justify an otherwise unrelated topic.
Physical furniture, home design and household fittings are not computing subjects.
Disambiguate the exact entity: furniture hardware is not computing hardware, stacking
cabinets is not software scaling, and locking shelves is not concurrency locking.
Shared words or source tags cannot establish a technical meaning absent from evidence.
Factual accuracy, popularity and valid citations alone do not establish relevance.
If the direct connection cannot be established, relevance is uncertain and automatic
approval is not permitted.
"""


def topic_scope_policy() -> str:
    if get_settings().product == "hardwaredaily":
        return """Hardware Today serves engineers and makers designing, building and testing
electronics and embedded systems. Consider components, sensors, power electronics, circuit
design, semiconductor manufacturing, instrumentation, firmware and embedded computing.
Evaluate the exact topic against primary evidence. A subject can qualify through hardware
engineering alone. A vendor name, promotional claim or incidental association is insufficient.
Financial results, dividends and unrelated consumer or software subjects are out of scope.
If the evidence cannot establish the exact identity and audience relevance, return uncertain.
"""
    return SCOPE_POLICY
