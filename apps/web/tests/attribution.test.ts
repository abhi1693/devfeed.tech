import { expect, it } from "vitest";
import { anonymousFeedDestination, attributionQuery } from "@/lib/attribution";

it.each(["x", "linkedin", "reddit", "mastodon", "bluesky", "newsletter"])(
  "preserves generic attribution from %s through the anonymous redirect",
  (source) => {
    const destination = anonymousFeedDestination({
      utm_source: source,
      utm_medium: "organic",
      utm_campaign: "Engineering notes / September",
      utm_content: "post & follow-up",
      utm_term: "C++",
      utm_id: "campaign-01",
    });
    const url = new URL(destination, "https://devfeed.tech");
    expect(url.pathname).toBe("/latest");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      utm_source: source,
      utm_medium: "organic",
      utm_campaign: "Engineering notes / September",
      utm_content: "post & follow-up",
      utm_term: "C++",
      utm_id: "campaign-01",
    });
  },
);

it("excludes arbitrary, ambiguous, oversized and control-character parameters", () => {
  expect(
    attributionQuery({
      utm_source: ["x", "linkedin"],
      utm_campaign: "a".repeat(201),
      utm_medium: "organic\n",
      utm_content: " ",
      return_to: "https://other.example",
      code: "private-code",
      utm_source_platform: "linkedin",
    }),
  ).toBe("utm_source_platform=linkedin");
});

it("keeps the existing untagged guest destination", () => {
  expect(anonymousFeedDestination({})).toBe("/latest");
});
