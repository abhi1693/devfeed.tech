import type { ConnectorConfig } from "@/lib/api/generated/models";

export function connectorDefaults(nick = false): ConnectorConfig {
  return {
    version: 1,
    base_url: nick ? "https://nicklaunches.com" : "",
    list_path: nick ? "/api/v1/products/" : "/products",
    detail_path: nick ? "/api/v1/products/{id}/" : null,
    items_paths: nick ? ["items", "results"] : ["items"],
    detail_root: "",
    fields: {
      external_id: [nick ? "slug" : "id"],
      name: ["name"],
      product_url: nick ? ["url", "productUrl"] : ["url"],
      listing_url: nick ? ["productUrl", "url"] : ["listing_url"],
      description: nick ? ["description", "tagline"] : ["description"],
      pricing: ["pricing"],
    },
    pagination: {
      mode: nick ? "cursor" : "none",
      parameter: "cursor",
      next_path: "nextCursor",
      size_parameter: "limit",
      page_size: 10,
      start: 1,
      total_path: null,
      has_more_path: null,
    },
    auth: { mode: "none", secret_ref: "", header: "X-API-Key" },
    filters: nick
      ? [{ path: "categories", values: ["Developer Tools"], include_missing: true }]
      : [],
    parameters: {},
    platform_hosts: nick ? ["nicklaunches.com", "www.nicklaunches.com"] : [],
    listing_url_template: null,
    attribution: nick ? "Via Nick Launches" : "",
    requests_per_minute: 60,
    timeout_seconds: 15,
    max_response_bytes: 2000000,
    max_pages: 1000,
  };
}
