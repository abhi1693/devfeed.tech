import { describe, expect, it } from "vitest";
import { responseMappings } from "@devfeed/ui/connector-response-mappings";
import { connectorDefaults } from "@/lib/partner-connectors";

describe("responseMappings", () => {
  it("matches fallback paths per product and numeric array segments without treating falsy data as absent", () => {
    const config = connectorDefaults();
    config.items_paths = ["missing", "results"];
    config.fields = { external_id: ["id"], name: ["title", "name"], pricing: ["plans.0.price"] };
    config.pagination = { ...config.pagination, has_more_path: "hasMore" };
    const mappings = responseMappings(config, {
      results: [
        { id: "one", title: "", name: "One", plans: [{ price: 0 }] },
        { id: "two", title: "Two" },
      ],
      hasMore: false,
    });
    expect(mappings.find((item) => item.field === "fields.name")?.addresses).toEqual([
      '["results","0","name"]',
      '["results","1","title"]',
    ]);
    expect(mappings.find((item) => item.field === "fields.pricing")?.addresses).toEqual([
      '["results","0","plans","0","price"]',
    ]);
    expect(mappings.find((item) => item.field === "pagination.has_more_path")?.addresses).toEqual([
      '["hasMore"]',
    ]);
  });
  it("supports a root array and leaves missing or empty values unmatched", () => {
    const config = connectorDefaults();
    config.items_paths = [""];
    config.fields = { name: ["name"], tagline: ["description"] };
    const mappings = responseMappings(config, [{ name: "One", description: null }, { name: "" }]);
    expect(mappings.find((item) => item.field === "items_paths")?.addresses).toEqual(["[]"]);
    expect(mappings.find((item) => item.field === "fields.name")?.addresses).toEqual([
      '["0","name"]',
    ]);
    expect(mappings.find((item) => item.field === "fields.tagline")?.addresses).toEqual([]);
  });

  it("distinguishes filter paths from matching scalar and array values", () => {
    const config = connectorDefaults();
    config.items_paths = ["items"];
    config.filters = [
      { path: "categories", values: [" developer tools "], include_missing: true },
      { path: "cost", values: ["42"], include_missing: true },
      { path: "enabled", values: ["FALSE"], include_missing: true },
    ];
    const mappings = responseMappings(config, {
      items: [
        { categories: ["Other", "Developer-Tools"], cost: 42, enabled: false },
        { categories: "DESIGN", cost: "42", enabled: true },
      ],
    });
    expect(mappings.find((item) => item.field === "filters.0.path")?.addresses).toEqual([
      '["items","0","categories"]',
      '["items","1","categories"]',
    ]);
    expect(mappings.find((item) => item.field === "filters.0.values")?.addresses).toEqual([
      '["items","0","categories","1"]',
    ]);
    expect(mappings.find((item) => item.field === "filters.1.values")?.addresses).toEqual([
      '["items","0","cost"]',
      '["items","1","cost"]',
    ]);
    expect(mappings.find((item) => item.field === "filters.2.values")?.addresses).toEqual([
      '["items","0","enabled"]',
    ]);
  });

  it("does not claim filter values matched when missing or null fields are allowed", () => {
    const config = connectorDefaults();
    config.items_paths = ["items"];
    config.filters = [{ path: "categories", values: ["Developer Tools"], include_missing: true }];
    const mappings = responseMappings(config, {
      items: [{}, { categories: null }, { categories: [null, { name: "Developer Tools" }] }],
    });
    expect(mappings.find((item) => item.field === "filters.0.values")?.addresses).toEqual([]);
  });
});
