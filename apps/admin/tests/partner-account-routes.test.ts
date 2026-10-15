import { isValidElement, type ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  partnerAccountHref,
  resolvePartnerAccountRoute,
  type PartnerAccountRoute,
} from "@/lib/partner-account-routes";

const navigation = vi.hoisted(() => ({
  requireAdmin: vi.fn(),
  notFound: vi.fn(() => {
    throw new Error("not found");
  }),
  redirect: vi.fn((destination: string) => {
    throw new Error(`redirect:${destination}`);
  }),
}));
vi.mock("next/navigation", () => navigation);
vi.mock("@/lib/server/session", () => ({ requireAdmin: navigation.requireAdmin }));
vi.mock("@/components/organisms/partner-account-page", () => ({
  PartnerAccountPage: () => null,
}));

import AccountPage from "@/app/(admin)/partnerships/accounts/[id]/[[...section]]/page";
import CreateAccountPage from "@/app/(admin)/partnerships/accounts/new/page";
import { PartnerAccountPage } from "@/components/organisms/partner-account-page";

const accountId = "account-1";
const identifier = "campaign:primary_2";
const routes: { segments?: string[]; route: PartnerAccountRoute; suffix: string }[] = [
  { route: { resource: "account", action: "view", accountId }, suffix: "" },
  { segments: [], route: { resource: "account", action: "view", accountId }, suffix: "" },
  {
    segments: ["details"],
    route: { resource: "account", action: "view", accountId },
    suffix: "",
  },
  {
    segments: ["edit"],
    route: { resource: "account", action: "edit", accountId },
    suffix: "/edit",
  },
  {
    segments: ["performance"],
    route: { resource: "performance", action: "list", accountId },
    suffix: "/performance",
  },
  {
    segments: ["products"],
    route: { resource: "product", action: "list", accountId },
    suffix: "/products",
  },
  {
    segments: ["campaigns"],
    route: { resource: "campaign", action: "list", accountId },
    suffix: "/campaigns",
  },
  {
    segments: ["campaigns", "new"],
    route: { resource: "campaign", action: "create", accountId },
    suffix: "/campaigns/new",
  },
  {
    segments: ["campaigns", identifier, "edit"],
    route: { resource: "campaign", action: "edit", accountId, identifier },
    suffix: "/campaigns/campaign%3Aprimary_2/edit",
  },
  {
    segments: ["members"],
    route: { resource: "member", action: "list", accountId },
    suffix: "/members",
  },
  {
    segments: ["members", "new"],
    route: { resource: "member", action: "create", accountId },
    suffix: "/members/new",
  },
  {
    segments: ["assets", "new"],
    route: { resource: "asset", action: "create", accountId },
    suffix: "/assets/new",
  },
  {
    segments: ["assets", "product:123e4567-e89b-12d3-a456-426614174000", "edit"],
    route: {
      resource: "asset",
      action: "edit",
      accountId,
      identifier: "product:123e4567-e89b-12d3-a456-426614174000",
    },
    suffix: "/assets/product%3A123e4567-e89b-12d3-a456-426614174000/edit",
  },
];

describe("partner account route decoding", () => {
  it.each(routes)("decodes $segments into $route.resource/$route.action", ({ segments, route }) => {
    expect(resolvePartnerAccountRoute(accountId, segments)).toEqual(route);
  });

  it.each(routes)(
    "links $route.resource/$route.action to its canonical location",
    ({ route, suffix }) => {
      expect(partnerAccountHref(route)).toBe(`/partnerships/accounts/${accountId}${suffix}`);
    },
  );

  it("links account creation without fabricating an account identifier", () => {
    expect(partnerAccountHref({ resource: "account", action: "create" })).toBe(
      "/partnerships/accounts/new",
    );
  });

  it.each(["123e4567-e89b-12d3-a456-426614174000", "partner_primary-2", "partner:primary"])(
    "accepts a safe opaque account identifier %s",
    (id) => {
      expect(resolvePartnerAccountRoute(id)).toEqual({
        resource: "account",
        action: "view",
        accountId: id,
      });
    },
  );

  it.each([
    [""],
    ["unknown"],
    ["details", "edit"],
    ["related"],
    ["related", "new"],
    ["products", "new"],
    ["members", "edit"],
    ["performance", "new"],
    ["assets"],
    ["campaigns", "one"],
    ["assets", "one", "view"],
    ["campaigns", "one", "delete"],
    ["assets", "one", "edit", "extra"],
    ["campaigns/new"],
    ["assets", "one/edit"],
  ])("rejects unsupported or ambiguous segments %j", (...segments) => {
    expect(resolvePartnerAccountRoute(accountId, segments)).toBeUndefined();
  });

  it.each([
    "",
    ".",
    "..",
    "../other",
    "one/two",
    "one\\two",
    "%2F",
    "%252F",
    "one?x=1",
    "one#two",
    "one\u0000two",
    "one\ntwo",
  ])("rejects unsafe account and child identifiers %j", (id) => {
    expect(resolvePartnerAccountRoute(id)).toBeUndefined();
    expect(resolvePartnerAccountRoute(accountId, ["campaigns", id, "edit"])).toBeUndefined();
    expect(resolvePartnerAccountRoute(accountId, ["assets", id, "edit"])).toBeUndefined();
  });
});

describe("partner account server pages", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    navigation.requireAdmin.mockResolvedValue({ roles: ["superuser"] });
  });

  const open = (
    segments?: string[],
    query: { offset?: string; limit?: string; days?: string; tab?: string } = {},
    id = accountId,
  ) =>
    AccountPage({
      params: Promise.resolve({ id, section: segments }),
      searchParams: Promise.resolve(query),
    });

  it.each(routes)(
    "forwards only decoded $route.resource/$route.action state",
    async ({ segments, route }) => {
      const element = await open(segments);
      expect(isValidElement(element)).toBe(true);
      expect(element.type).toBe(PartnerAccountPage);
      expect(element.props).toEqual({
        route,
        initialOffset: 0,
        initialLimit: 100,
        initialDays: 30,
        initialSettingsTab: "general",
      });
      expect(navigation.requireAdmin).toHaveBeenCalledOnce();
    },
  );

  it("passes validated pagination to the performance reporting table", async () => {
    const element = await open(["performance"], { offset: "200", limit: "50" });
    expect(element.props).toEqual({
      route: { resource: "performance", action: "list", accountId },
      initialOffset: 200,
      initialLimit: 50,
      initialDays: 30,
      initialSettingsTab: "general",
    });
  });

  it.each(["7", "30", "90"])("restores the reporting period %s", async (days) => {
    const element = await open(["performance"], { days });
    expect(element.props.initialDays).toBe(Number(days));
  });

  it.each(["general", "delivery", "formats", "audience"])(
    "opens the bookmarked settings tab %s",
    async (tab) => {
      const element = await open(["edit"], { tab });
      expect(element.props.initialSettingsTab).toBe(tab);
    },
  );

  it.each([
    { offset: "-1" },
    { offset: "1.5" },
    { offset: "NaN" },
    { offset: "9007199254740992" },
    { limit: "0" },
    { limit: "201" },
    { limit: "1.5" },
    { limit: "Infinity" },
    { days: "8" },
    { days: "7.5" },
    { days: "NaN" },
    { days: "Infinity" },
    { tab: "unknown" },
    { tab: "../delivery" },
  ])("rejects invalid pagination %j", async (query) => {
    await expect(open(["performance"], query)).rejects.toThrow("not found");
    expect(navigation.notFound).toHaveBeenCalledOnce();
  });

  it.each([
    { id: "../other", segments: undefined },
    { id: accountId, segments: ["assets", "%2F", "edit"] },
    { id: accountId, segments: ["campaigns", "one", "delete"] },
  ])("returns not found before rendering invalid route %j", async ({ id, segments }) => {
    await expect(open(segments, {}, id)).rejects.toThrow("not found");
    expect(navigation.notFound).toHaveBeenCalledOnce();
  });

  it("declares account creation explicitly", async () => {
    const element = (await CreateAccountPage()) as ReactElement<{ route: PartnerAccountRoute }>;
    expect(element.type).toBe(PartnerAccountPage);
    expect(element.props).toEqual({ route: { resource: "account", action: "create" } });
  });

  it.each([open, () => CreateAccountPage()])(
    "rejects an authenticated non-superuser",
    async (page) => {
      navigation.requireAdmin.mockResolvedValue({ roles: ["reader"] });
      await expect(page()).rejects.toThrow("redirect:/");
      expect(navigation.redirect).toHaveBeenCalledWith("/");
    },
  );

  it.each([open, () => CreateAccountPage()])("preserves the login guard", async (page) => {
    navigation.requireAdmin.mockRejectedValue(new Error("redirect:/login"));
    await expect(page()).rejects.toThrow("redirect:/login");
    expect(navigation.notFound).not.toHaveBeenCalled();
  });
});
