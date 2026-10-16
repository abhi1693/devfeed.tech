import { beforeEach, expect, it, vi } from "vitest";
import Page, { dynamic } from "../src/app/settings/[accountId]/[settingsSection]/page";
import { PortalPage } from "../src/lib/server/portal-page";
import { accountSettingsPath, type AccountSettingsSection } from "../src/lib/routes";

const { authorize } = vi.hoisted(() => ({ authorize: vi.fn() }));
vi.mock("../src/lib/server/portal-page", () => ({ PortalPage: authorize }));
beforeEach(() => authorize.mockReset());

const accountId = "22222222-2222-4222-8222-222222222222";

it.each(["destination", "formats", "audience"])(
  "delegates the %s bookmark to the authorized account page without losing list context",
  async (settingsSection) => {
    const searchParams = Promise.resolve({ account_offset: "100", q: "Studio" });
    const page = await Page({
      params: Promise.resolve({ accountId, settingsSection }),
      searchParams,
    });
    expect(page.type).toBe(PortalPage);
    expect(page.props).toEqual({
      section: "settings",
      settingsSection,
      accountId,
      searchParams,
    });
    expect(dynamic).toBe("force-dynamic");
  },
);

it.each([
  "general",
  "private",
  "Destination",
  "destination/extra",
  "..",
  "%2Faudience",
  "",
  "unknown",
])(
  "rejects unsupported settings section %j before account authorization",
  async (settingsSection) => {
    await expect(
      Page({
        params: Promise.resolve({ accountId, settingsSection }),
        searchParams: Promise.resolve({}),
      }),
    ).rejects.toThrow("NEXT_HTTP_ERROR_FALLBACK;404");
    expect(authorize).not.toHaveBeenCalled();
  },
);

it.each([
  [undefined, `/settings/${accountId}`],
  ["general", `/settings/${accountId}`],
  ["destination", `/settings/${accountId}/destination`],
  ["formats", `/settings/${accountId}/formats`],
  ["audience", `/settings/${accountId}/audience`],
] satisfies [AccountSettingsSection | undefined, string][])(
  "generates the canonical %s account settings route",
  (section, expected) => expect(accountSettingsPath(accountId, section)).toBe(expected),
);

it("keeps account identifiers inside one URL segment", () => {
  expect(accountSettingsPath("account/other?role=admin", "audience")).toBe(
    "/settings/account%2Fother%3Frole%3Dadmin/audience",
  );
});
