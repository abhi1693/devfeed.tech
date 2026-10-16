import { expect, it } from "vitest";
import { AD_LINK_REL_OPTIONS, adLinkRel, parseAdLinkRel } from "@devfeed/ui/ad-link-rel";
import { parsePartnerPlacements } from "@/lib/partner-placements";
import { article } from "./fixtures";
import { placement, sponsoredPlacement } from "./partner-placement-fixtures";

it.each(
  [["nofollow"], ["sponsored", "external"], [...AD_LINK_REL_OPTIONS]].map((relations) => ({
    relations,
  })),
)(
  "retains server-selected relations and independent outbound protections (%j)",
  ({ relations }) => {
    const tokens = parseAdLinkRel(relations);
    const [item] = parsePartnerPlacements({ items: [{ ...placement, link_rel: tokens }] });
    expect(item.link_rel).toEqual(tokens);
    expect(adLinkRel(item.link_rel)).toBe([...tokens, "noopener", "noreferrer"].join(" "));
  },
);

it("defaults older delivery payloads and unconfigured article links to nofollow", () => {
  expect(
    parsePartnerPlacements({ items: [{ ...placement, link_rel: undefined }] })[0].link_rel,
  ).toEqual(["nofollow"]);
  expect(adLinkRel()).toBe("nofollow noopener noreferrer");
  expect(adLinkRel([], false)).toBe("sponsored");
});

it.each(
  [null, "nofollow", ["opener"], ["follow"], ["nofollow sponsored"], ["nofollow", "nofollow"]].map(
    (link_rel) => ({ link_rel }),
  ),
)("rejects malformed delivery relations (%j)", ({ link_rel }) => {
  expect(() => parsePartnerPlacements({ items: [{ ...placement, link_rel }] })).toThrow();
});

it("retains effective product policy on the article cached for its sponsored preview", () => {
  const [item] = parsePartnerPlacements({
    items: [
      {
        ...sponsoredPlacement,
        link_rel: ["sponsored", "external"],
        ad_format: "sponsored_content",
        url: `/articles/${article.slug}`,
        article: { ...article, topics: [] },
      },
    ],
  });
  expect(item.article?.sponsor_link_rel).toEqual(["sponsored", "external"]);
});

it.each([
  { relations: [], expected: ["sponsored"] },
  { relations: ["external"], expected: ["external", "sponsored"] },
  { relations: ["ugc"], expected: ["ugc", "sponsored"] },
  { relations: ["external", "ugc"], expected: ["external", "ugc", "sponsored"] },
])(
  "qualifies older unprotected advertising payloads without mutating saved choices: $relations",
  ({ relations, expected }) => {
    const tokens = parseAdLinkRel(relations);
    const [item] = parsePartnerPlacements({ items: [{ ...placement, link_rel: tokens }] });
    expect(adLinkRel(item.link_rel)).toBe([...expected, "noopener", "noreferrer"].join(" "));
    expect(adLinkRel(item.link_rel, false)).toBe(expected.join(" "));
    expect(item.link_rel).toEqual(relations);
  },
);
