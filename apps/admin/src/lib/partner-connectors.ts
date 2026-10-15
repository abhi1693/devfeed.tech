import type { ConnectorConfig } from "@/lib/api/generated/models";

export function connectorDefaults(provider = ""): ConnectorConfig {
  const nick = provider === "nick-launches";
  const shipyard = provider === "shipyardhq";
  return {
    version: 1,
    base_url: nick ? "https://nicklaunches.com" : shipyard ? "https://shipyardhq.dev" : "",
    list_path: nick ? "/api/v1/products/" : shipyard ? "/api/products" : "",
    detail_path: nick ? "/api/v1/products/{id}/" : null,
    items_paths: nick ? ["items", "results"] : shipyard ? ["items"] : [""],
    detail_root: "",
    fields: {
      external_id: nick ? ["slug"] : shipyard ? ["id"] : [],
      name: nick || shipyard ? ["name"] : [],
      product_url: nick ? ["url", "productUrl"] : shipyard ? ["productUrl"] : [],
      listing_url: nick ? ["productUrl", "url"] : shipyard ? ["url"] : [],
      tagline: nick || shipyard ? ["tagline", "description"] : [],
      pricing: shipyard ? ["pricing.model"] : nick ? ["pricing"] : [],
    },
    pagination: {
      mode: nick || shipyard ? "cursor" : "none",
      parameter: nick || shipyard ? "cursor" : null,
      next_path: nick || shipyard ? "nextCursor" : "",
      size_parameter: nick || shipyard ? "limit" : null,
      page_size: 10,
      start: 1,
      total_path: null,
      has_more_path: null,
    },
    auth: {
      mode: shipyard ? "bearer" : "none",
      secret_ref: shipyard ? "DEVFEED_PARTNER_SECRET_PRODUCTS_API_TOKEN" : "",
      header: "X-API-Key",
    },
    filters: nick
      ? [{ path: "categories", values: ["Developer Tools"], include_missing: true }]
      : [],
    parameters: {},
    platform_hosts: nick
      ? ["nicklaunches.com", "www.nicklaunches.com"]
      : shipyard
        ? ["shipyardhq.dev", "www.shipyardhq.dev"]
        : [],
    listing_url_template: null,
    attribution: nick ? "Via Nick Launches" : shipyard ? "Via ShipyardHQ" : "",
    requests_per_minute: 60,
    timeout_seconds: 15,
    max_response_bytes: 2000000,
    max_pages: 1000,
  };
}
