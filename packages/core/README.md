# Shared backend core

See [backend service boundaries](ARCHITECTURE.md) for dependency ownership,
durable worker contracts and the path towards separate domain services.

## Topic branding

`DEVFEED_AUTO_RESEARCH_TOPIC_BRANDING=true` enables a separate backfill for active
topics without `logo_url`, across every canonical kind, including platforms.
It also picks up newly approved topics after minimal identity review. The setting
defaults to false and requires AI configuration. At most two branding research or
verification runs are outstanding; each topic receives one automatic attempt.

Branding creates an audited update proposal and researches only a missing logo and
missing official website. It preserves identity, kind, description, aliases,
keywords and facts. Direct assets pass the public-network transport guards, a
2 MB limit and an image response check. Citations and the complete draft then
require independent verification before `DEVFEED_AUTO_APPROVE_TOPICS` can apply
the update. Otherwise the proposal awaits manual review. Concurrent catalog edits
block approval through the existing baseline check.

The research and verification calls each have a 16,000-token guard and a two-search
guard; existing finite job retries apply. Failed and inconclusive attempts remain
visible for manual review and are not automatically rescheduled. Operators can
explicitly request research again on a pending branding proposal. Polling never
resets the identity-decision budget. Branding proposals do not enter the minimal
identity or correction workflows, which would discard optional metadata.

Topics without a verified standalone logo retain a null URL and an explanation in
the research result. Parent-company logos, related products and generic icons must
not substitute for the exact entity. Existing logos are not overwritten.

No schema migration or API contract change is required. Enable the setting only
after the scheduler, topic-analysis and research-verification workers all run the
new code. Roll back by disabling admission first and draining branding jobs before
reverting workers: older bounded workers do not recognize branding-only requests.
