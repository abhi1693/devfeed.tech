import type { Account, Identity } from "../src/lib/types";

export function partnerAccount(overrides: Partial<Account> = {}): Account {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    name: "Shipyard",
    tier: "gold",
    status: "active",
    benefits: [],
    organic_enabled: true,
    auto_include_products: true,
    promotion_surfaces: ["article", "topic", "feed"],
    promotion_kind: null,
    allowed_promotion_kinds: ["product", "ad"],
    effective_promotion_kind: "ad",
    promotion_kind_source: "tier",
    approval_mode: "qualified",
    outbound_destination: "product_url",
    link_rel: ["nofollow"],
    configurable_link_rel: false,
    link_rel_options: ["nofollow", "sponsored", "ugc", "external"],
    advanced_targeting: false,
    audience: { experience_levels: [], language_topic_ids: [], tool_topic_ids: [] },
    ad_formats: ["feed"],
    allowed_ad_formats: ["feed", "header_sponsorship", "engagement", "sponsored_content"],
    ...overrides,
  };
}

export function partnerIdentity(overrides: Partial<Identity> = {}): Identity {
  return {
    subject: "alice",
    issuer: "https://identity.example",
    organization_id: "devfeed",
    name: "Alice",
    email: null,
    roles: ["partner"],
    expires_at: 4_102_444_800,
    csrf_token: "csrf",
    ...overrides,
  };
}
