import { describe, expect, it } from "vitest";
import { responseMappings } from "@/lib/partner-response-mappings";
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
    config.fields = { name: ["name"], description: ["description"] };
    const mappings = responseMappings(config, [{ name: "One", description: null }, { name: "" }]);
    expect(mappings.find((item) => item.field === "items_paths")?.addresses).toEqual(["[]"]);
    expect(mappings.find((item) => item.field === "fields.name")?.addresses).toEqual([
      '["0","name"]',
    ]);
    expect(mappings.find((item) => item.field === "fields.description")?.addresses).toEqual([]);
  });
});
