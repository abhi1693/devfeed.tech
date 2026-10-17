// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PartnerPlacements } from "@/components/partner-placements";
import { readerRequest } from "@/lib/reader-runtime";
import { userRequest } from "@/lib/user";
import { observePartnerPlacement } from "@/lib/partner-tracking";
import { partnerReceipt, placement, sponsoredPlacement } from "./partner-placement-fixtures";

function refreshReceipts() {
  placement.receipt = partnerReceipt({ assetId: placement.asset_id });
  sponsoredPlacement.receipt = partnerReceipt({ assetId: sponsoredPlacement.asset_id });
}

function usePlacementClock() {
  vi.useFakeTimers();
  vi.setSystemTime(Math.floor(Date.now() / 1000) * 1000);
  refreshReceipts();
}

const session = vi.hoisted(() => ({
  user: null as null | { user_id: string; csrf_token: string },
  loading: false,
  sessionRevision: 0,
}));
const prompts = vi.hoisted(() => ({
  current: null as null | { ready: boolean; modalOpen: boolean },
}));
vi.mock("@/components/user-account", () => ({ useUser: () => session }));
vi.mock("@/components/reader-prompts", () => ({
  useReaderPromptCoordinator: () => prompts.current,
}));
vi.mock("@/lib/reader-runtime", () => ({ readerRequest: vi.fn() }));
vi.mock("@/lib/user", () => ({ userRequest: vi.fn() }));
vi.mock("@/lib/partner-tracking", () => ({ observePartnerPlacement: vi.fn(() => () => {}) }));
beforeEach(() => {
  session.user = null;
  session.loading = false;
  session.sessionRevision = 0;
  prompts.current = null;
  refreshReceipts();
  vi.mocked(readerRequest).mockImplementation(async () =>
    Response.json({ items: [placement, sponsoredPlacement] }),
  );
  vi.mocked(userRequest).mockResolvedValue({ items: [placement] });
  vi.mocked(observePartnerPlacement)
    .mockReset()
    .mockImplementation(() => () => {});
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

it("shows the importing platform's responsive logo beside its name in native grid metadata", async () => {
  const platform = {
    name: "Launch platform",
    logo_url: "https://images.example/platform/32.webp?v=one",
    logo_variants: [16, 32, 64, 96].map((width) => ({
      width,
      url: `https://images.example/platform/${width}.webp?v=one`,
    })),
  };
  vi.mocked(readerRequest).mockResolvedValueOnce(
    Response.json({ items: [{ ...placement, platform }] }),
  );
  render(<PartnerPlacements surface="feed">{(cards) => <div>{cards}</div>}</PartnerPlacements>);
  const card = (await screen.findByRole("link", { name: placement.name })).closest("article")!;
  const source = card.querySelector(".card-source")!;
  const logo = source.querySelector("img")!;
  expect(source.textContent).toBe(platform.name);
  expect(source.textContent).not.toContain(placement.partner_name);
  expect(logo.getAttribute("src")).toBe(platform.logo_url);
  expect(logo.getAttribute("srcset")).toBe(
    platform.logo_variants.map(({ url, width }) => `${url} ${width}w`).join(", "),
  );
  expect(logo.getAttribute("sizes")).toBe("12px");
  expect(logo.getAttribute("alt")).toBe("");
  expect(logo.getAttribute("referrerpolicy")).toBe("no-referrer");
  expect(source.querySelector(".source-avatar")?.getAttribute("aria-hidden")).toBe("true");
  fireEvent.error(logo);
  expect(source.querySelector("img")).toBeNull();
  expect(source.querySelector("svg")).toBeTruthy();
  expect(source.textContent).toBe(platform.name);
  expect(card.querySelector(".partner-placement-logo img")?.getAttribute("src")).toBe(
    placement.logo_url,
  );
});

it("keeps a manual product's account attribution and a neutral source icon without a platform", async () => {
  vi.mocked(readerRequest).mockResolvedValueOnce(Response.json({ items: [placement] }));
  render(<PartnerPlacements surface="feed">{(cards) => <div>{cards}</div>}</PartnerPlacements>);
  const card = (await screen.findByRole("link", { name: placement.name })).closest("article")!;
  const source = card.querySelector(".card-source")!;
  expect(source.textContent).toBe(placement.partner_name);
  expect(source.querySelector("img")).toBeNull();
  expect(source.querySelector("svg")).toBeTruthy();
});

it.each(["feed", "engagement", "header_sponsorship"] as const)(
  "shows defined listing attribution as text in %s ads without changing links or disclosure",
  async (ad_format) => {
    const attribution = "Via Nick Launches <img src=x onerror=alert(1)>";
    const item = {
      ...placement,
      ad_format,
      attribution: `  ${attribution}  `,
      platform: { name: "Nick Launches", logo_url: null, logo_variants: [] },
    };
    vi.mocked(readerRequest).mockResolvedValueOnce(Response.json({ items: [item] }));
    render(
      <PartnerPlacements
        surface={ad_format === "header_sponsorship" ? "header" : "feed"}
        ad_formats={[ad_format]}
      >
        {(cards) => <div>{cards}</div>}
      </PartnerPlacements>,
    );
    const link = await screen.findByRole("link", { name: placement.name });
    expect(link.textContent).toContain(attribution);
    expect(link.querySelector("[onerror]")).toBeNull();
    expect(link.getAttribute("href")).toBe(placement.url);
    expect(screen.getByText("Sponsored")).toBeTruthy();
    await waitFor(() =>
      expect(observePartnerPlacement).toHaveBeenCalledWith(link, item.receipt, expect.any(Object)),
    );
    if (ad_format === "feed")
      expect(link.querySelector(".card-source")?.textContent).toBe(item.platform.name);
    if (ad_format !== "header_sponsorship")
      expect(link.querySelector(".partner-placement-destination")?.textContent).toBe(attribution);
  },
);

it.each([undefined, null, "", "   "])(
  "keeps the platform name when listing attribution is absent (%j)",
  async (attribution) => {
    const platform = { name: "Nick Launches", logo_url: null, logo_variants: [] };
    vi.mocked(readerRequest).mockResolvedValueOnce(
      Response.json({ items: [{ ...placement, platform, attribution }] }),
    );
    render(<PartnerPlacements surface="feed">{(cards) => <div>{cards}</div>}</PartnerPlacements>);
    const link = await screen.findByRole("link", { name: placement.name });
    expect(link.querySelector(".card-source")?.textContent).toBe(platform.name);
    expect(link.querySelector(".partner-placement-destination")?.textContent).toBe("");
  },
);

it("keeps Engagement linked and tracked without an empty attribution footer", async () => {
  vi.mocked(readerRequest).mockResolvedValueOnce(
    Response.json({ items: [{ ...placement, ad_format: "engagement" }] }),
  );
  render(
    <PartnerPlacements
      surface="article"
      article_id={placement.product_id}
      ad_formats={["engagement"]}
    />,
  );
  const link = await screen.findByRole("link", { name: placement.name });
  expect(link.querySelector(".partner-placement-destination")).toBeNull();
  expect(link.textContent).not.toContain("Explore");
  expect(link.getAttribute("href")).toBe(placement.url);
  expect(link.getAttribute("rel")).toBe("nofollow noopener noreferrer");
  expect(link.closest("article")?.querySelectorAll("a")).toHaveLength(1);
  await waitFor(() =>
    expect(observePartnerPlacement).toHaveBeenCalledWith(
      link,
      placement.receipt,
      expect.any(Object),
    ),
  );
});

it.each(["feed", "header_sponsorship", "engagement"] as const)(
  "uses configured relations on %s without losing disclosure or signed tracking",
  async (ad_format) => {
    const item = { ...placement, ad_format, link_rel: ["sponsored", "external"] };
    vi.mocked(readerRequest).mockResolvedValueOnce(Response.json({ items: [item] }));
    render(
      <PartnerPlacements
        surface={ad_format === "header_sponsorship" ? "header" : "feed"}
        ad_formats={[ad_format]}
      >
        {(cards) => <div>{cards}</div>}
      </PartnerPlacements>,
    );
    const link = await screen.findByRole("link", { name: item.name });
    expect(link.getAttribute("rel")).toBe("sponsored external noopener noreferrer");
    expect(screen.getByText("Sponsored")).toBeTruthy();
    await waitFor(() =>
      expect(observePartnerPlacement).toHaveBeenCalledWith(link, item.receipt, expect.any(Object)),
    );
  },
);

it("expires continuously mounted compact products without refetching or a parent rerender", async () => {
  usePlacementClock();
  const stopTracking = vi.fn();
  vi.mocked(observePartnerPlacement).mockReturnValue(stopTracking);
  await act(async () => {
    render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  });
  expect(screen.getByRole("link", { name: placement.name })).toBeDefined();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60 * 60_000 - 1);
  });
  expect(screen.getByRole("link", { name: placement.name })).toBeDefined();
  expect(stopTracking).not.toHaveBeenCalled();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(screen.queryByRole("link", { name: placement.name })).toBeNull();
  expect(screen.queryByRole("link", { name: sponsoredPlacement.name })).toBeNull();
  expect(stopTracking).toHaveBeenCalledTimes(2);
  expect(readerRequest).toHaveBeenCalledTimes(1);
});

it.each(["context", "account"])(
  "cancels the previous expiry when the delivery changes its %s",
  async (change) => {
    usePlacementClock();
    const stopped = new Map<string, ReturnType<typeof vi.fn>>();
    vi.mocked(observePartnerPlacement).mockImplementation((_element, receipt) => {
      const stop = vi.fn();
      stopped.set(receipt, stop);
      return stop;
    });
    let articleId = placement.product_id;
    const content = () => <PartnerPlacements surface="article" article_id={articleId} />;
    let mounted!: ReturnType<typeof render>;
    await act(async () => {
      mounted = render(content());
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30 * 60_000);
    });
    const renewed = { ...placement, receipt: partnerReceipt({ assetId: placement.asset_id }) };
    if (change === "context") {
      articleId = sponsoredPlacement.product_id;
      vi.mocked(readerRequest).mockResolvedValueOnce(Response.json({ items: [renewed] }));
    } else {
      session.user = { user_id: "new-reader", csrf_token: "new-csrf" };
      session.sessionRevision += 1;
      vi.mocked(userRequest).mockResolvedValueOnce({ items: [renewed] });
    }
    await act(async () => mounted.rerender(content()));
    expect(stopped.get(placement.receipt)).toHaveBeenCalledTimes(1);
    expect(stopped.get(sponsoredPlacement.receipt)).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30 * 60_000);
    });
    expect(screen.getByRole("link", { name: placement.name })).toBeDefined();
    expect(stopped.get(renewed.receipt)).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30 * 60_000);
    });
    expect(screen.queryByRole("link", { name: placement.name })).toBeNull();
    expect(stopped.get(renewed.receipt)).toHaveBeenCalledTimes(1);
    expect(readerRequest).toHaveBeenCalledTimes(change === "context" ? 2 : 1);
    expect(userRequest).toHaveBeenCalledTimes(change === "account" ? 1 : 0);
  },
);

it("cancels expiry work and tracking when unmounted", async () => {
  usePlacementClock();
  const stopTracking = vi.fn();
  const beforeDisplay = vi.fn();
  const afterDisplay = vi.fn();
  vi.mocked(observePartnerPlacement).mockReturnValue(stopTracking);
  let mounted!: ReturnType<typeof render>;
  await act(async () => {
    mounted = render(
      <PartnerPlacements surface="feed" beforeDisplay={beforeDisplay} afterDisplay={afterDisplay}>
        {(cards) => <div>{cards}</div>}
      </PartnerPlacements>,
    );
  });
  mounted.unmount();
  expect(stopTracking).toHaveBeenCalledTimes(2);
  beforeDisplay.mockClear();
  afterDisplay.mockClear();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2 * 60 * 60_000);
    document.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("focus"));
    window.dispatchEvent(new Event("pageshow"));
  });
  expect(beforeDisplay).not.toHaveBeenCalled();
  expect(afterDisplay).not.toHaveBeenCalled();
  expect(stopTracking).toHaveBeenCalledTimes(2);
  expect(readerRequest).toHaveBeenCalledTimes(1);
});

it.each(["active", "verification", "modal"])(
  "preserves feed cells and reading hooks when a delivery expires during %s",
  async (state) => {
    usePlacementClock();
    const stopTracking = vi.fn();
    vi.mocked(observePartnerPlacement).mockReturnValue(stopTracking);
    const beforeLinks: number[] = [];
    const afterLinks: number[] = [];
    const beforeDisplay = vi.fn(() => {
      beforeLinks.push(screen.queryAllByRole("link").length);
    });
    const afterDisplay = vi.fn(() => {
      afterLinks.push(screen.queryAllByRole("link").length);
    });
    const content = () => (
      <PartnerPlacements surface="feed" beforeDisplay={beforeDisplay} afterDisplay={afterDisplay}>
        {(cards) => (
          <div data-testid="feed-cells">
            <article>Earlier article</article>
            {cards[0]}
            <article>Current reading position</article>
            {cards[1]}
            <article>Later article</article>
          </div>
        )}
      </PartnerPlacements>
    );
    let mounted!: ReturnType<typeof render>;
    await act(async () => {
      mounted = render(content());
    });
    const cells = Array.from(screen.getByTestId("feed-cells").children);
    expect(cells).toHaveLength(5);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30 * 60_000);
    });
    if (state === "verification") session.loading = true;
    if (state === "modal") prompts.current = { ready: true, modalOpen: true };
    if (state !== "active") await act(async () => mounted.rerender(content()));
    beforeDisplay.mockClear();
    afterDisplay.mockClear();
    beforeLinks.length = afterLinks.length = 0;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30 * 60_000);
    });

    const retained = Array.from(screen.getByTestId("feed-cells").children);
    expect(retained).toHaveLength(cells.length);
    retained.forEach((cell, index) => expect(cell).toBe(cells[index]));
    for (const cell of [cells[1], cells[3]]) {
      expect(cell.getAttribute("data-suspended")).toBe("true");
      expect(cell.getAttribute("aria-hidden")).toBe("true");
      expect(cell.hasAttribute("inert")).toBe(true);
      expect(cell.hasAttribute("hidden")).toBe(false);
    }
    expect(screen.queryByRole("link", { name: placement.name })).toBeNull();
    expect(stopTracking).toHaveBeenCalledTimes(2);
    expect(beforeDisplay).toHaveBeenCalledTimes(1);
    expect(afterDisplay).toHaveBeenCalledTimes(1);
    expect(beforeLinks).toEqual([state === "active" ? 2 : 0]);
    expect(afterLinks).toEqual([0]);

    session.loading = false;
    prompts.current = null;
    await act(async () => mounted.rerender(content()));
    expect(screen.queryByRole("link", { name: placement.name })).toBeNull();
    expect(observePartnerPlacement).toHaveBeenCalledTimes(2);
    expect(readerRequest).toHaveBeenCalledTimes(1);
  },
);

it.each(["visibilitychange", "focus", "pageshow"])(
  "checks the actual receipt deadline on %s when a background timer has not fired",
  async (event) => {
    usePlacementClock();
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    const stopTracking = vi.fn();
    vi.mocked(observePartnerPlacement).mockReturnValue(stopTracking);
    await act(async () => {
      render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
    });
    vi.setSystemTime(Date.now() + 60 * 60_000);
    expect(screen.getByRole("link", { name: placement.name })).toBeDefined();
    await act(async () => {
      visibility.mockReturnValue("visible");
      (event === "visibilitychange" ? document : window).dispatchEvent(new Event(event));
    });
    expect(screen.queryByRole("link", { name: placement.name })).toBeNull();
    expect(stopTracking).toHaveBeenCalledTimes(2);
    expect(readerRequest).toHaveBeenCalledTimes(1);
  },
);

it("uses the earliest signed issuance deadline instead of restarting an hour after loading", async () => {
  usePlacementClock();
  const older = {
    ...placement,
    receipt: partnerReceipt({ assetId: placement.asset_id, issuedAt: Date.now() - 59 * 60_000 }),
  };
  vi.mocked(readerRequest).mockResolvedValueOnce(
    Response.json({ items: [older, sponsoredPlacement] }),
  );
  await act(async () => {
    render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60_000 - 1);
  });
  expect(screen.getAllByRole("link")).toHaveLength(2);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(screen.queryAllByRole("link")).toHaveLength(0);
  expect(readerRequest).toHaveBeenCalledTimes(1);
});

it.each(["malformed", "expired"])(
  "does not show or observe a delivery with a %s receipt",
  async (kind) => {
    usePlacementClock();
    const item = {
      ...placement,
      receipt:
        kind === "malformed"
          ? "unusable-delivery-receipt"
          : partnerReceipt({ issuedAt: Date.now() - 60 * 60_000 }),
    };
    vi.mocked(readerRequest).mockResolvedValueOnce(Response.json({ items: [item] }));
    await act(async () => {
      render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
    });
    expect(screen.queryByRole("link", { name: placement.name })).toBeNull();
    expect(observePartnerPlacement).not.toHaveBeenCalled();
  },
);

it("rejects a response whose receipt expired while the request was suspended", async () => {
  usePlacementClock();
  let finish!: (response: Response) => void;
  vi.mocked(readerRequest).mockImplementationOnce(
    () => new Promise<Response>((resolve) => (finish = resolve)),
  );
  await act(async () => {
    render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  });
  vi.setSystemTime(Date.now() + 60 * 60_000);
  await act(async () => finish(Response.json({ items: [placement] })));
  expect(screen.queryByRole("link", { name: placement.name })).toBeNull();
  expect(observePartnerPlacement).not.toHaveBeenCalled();
  expect(readerRequest).toHaveBeenCalledTimes(1);
});

it("releases expired product reservations while retaining their inert feed cells", async () => {
  usePlacementClock();
  let showFeed = true;
  let showTopic = false;
  let showArticle = false;
  const content = () => (
    <>
      {showFeed && (
        <div key="feed" data-testid="expired-feed">
          <PartnerPlacements surface="feed">{(cards) => <div>{cards}</div>}</PartnerPlacements>
        </div>
      )}
      {showTopic && (
        <PartnerPlacements key="topic" surface="topic" topic_id={placement.product_id} />
      )}
      {showArticle && (
        <PartnerPlacements
          key="article"
          surface="article"
          article_id={sponsoredPlacement.product_id}
        />
      )}
    </>
  );
  let mounted!: ReturnType<typeof render>;
  await act(async () => {
    mounted = render(content());
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60 * 60_000);
  });
  refreshReceipts();
  showTopic = true;
  await act(async () => mounted.rerender(content()));
  expect(screen.getByRole("link", { name: placement.name })).toBeDefined();
  expect(screen.getByTestId("expired-feed").querySelectorAll("[data-suspended]")).toHaveLength(2);
  const query = JSON.parse(String(vi.mocked(readerRequest).mock.calls[1][1]?.body));
  expect(query.exclude_product_ids).toEqual([]);
  expect(readerRequest).toHaveBeenCalledTimes(2);
  showFeed = false;
  showArticle = true;
  await act(async () => mounted.rerender(content()));
  expect(screen.getAllByRole("link", { name: placement.name })).toHaveLength(1);
  expect(screen.getAllByRole("link", { name: sponsoredPlacement.name })).toHaveLength(1);
  const nextQuery = JSON.parse(String(vi.mocked(readerRequest).mock.calls[2][1]?.body));
  expect(new Set(nextQuery.exclude_product_ids)).toEqual(
    new Set([placement.product_id, sponsoredPlacement.product_id]),
  );
});

it("discloses every compact product as sponsored while retaining its signed asset tracking", async () => {
  render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await screen.findByRole("link", { name: placement.name });
  expect(screen.getAllByText("Sponsored")).toHaveLength(2);
  expect(screen.queryByText(placement.reason)).toBeNull();
  expect(screen.queryByText("Relevant product")).toBeNull();
  expect(screen.queryByText(`From ${placement.partner_name}`)).toBeNull();
  const card = screen.getByRole("link", { name: placement.name }).closest(".partner-placement")!;
  const logo = card.querySelector("img")!;
  expect(logo.getAttribute("src")).toBe(placement.logo_url);
  expect(logo.getAttribute("referrerpolicy")).toBe("no-referrer");
  expect(logo.getAttribute("alt")).toBe("");
  expect(screen.getByRole("link", { name: sponsoredPlacement.name }).getAttribute("rel")).toBe(
    "nofollow noopener noreferrer",
  );
  await waitFor(() => expect(observePartnerPlacement).toHaveBeenCalledTimes(2));
  expect(vi.mocked(readerRequest).mock.calls[0][1]).toMatchObject({
    credentials: "omit",
    cache: "no-store",
    referrerPolicy: "no-referrer",
  });
  expect(JSON.parse(vi.mocked(readerRequest).mock.calls[0][1]!.body as string)).toEqual({
    surface: "article",
    article_id: placement.product_id,
    exclude_product_ids: [],
  });
});

it("keeps header sponsorship compact with a destination title", async () => {
  vi.mocked(readerRequest).mockResolvedValueOnce(
    Response.json({ items: [{ ...placement, ad_format: "header_sponsorship" }] }),
  );
  render(<PartnerPlacements surface="header" ad_formats={["header_sponsorship"]} />);
  const link = await screen.findByRole("link", { name: placement.name });
  expect(link.querySelector(".partner-placement-description")?.textContent).toBe(placement.tagline);
  expect(link.querySelector(".partner-placement-destination")).toBeNull();
  expect(link.title).toBe(`${placement.name} · partner.example`);
  expect(screen.getByText("Sponsored")).toBeDefined();
  expect(link.querySelector(".card-image")).toBeNull();
});

it("replaces broken or absent product logos with stable initials without hiding the product", async () => {
  vi.mocked(readerRequest).mockResolvedValueOnce(
    Response.json({ items: [placement, { ...sponsoredPlacement, logo_url: null }] }),
  );
  render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  const title = await screen.findByRole("link", { name: placement.name });
  const card = title.closest(".partner-placement")!;
  fireEvent.error(card.querySelector("img")!);
  expect(card.querySelector("img")).toBeNull();
  expect(card.querySelector(".partner-placement-logo")?.textContent).toBeTruthy();
  const paidCard = screen
    .getByRole("link", { name: sponsoredPlacement.name })
    .closest(".partner-placement")!;
  expect(paidCard.querySelector("img")).toBeNull();
  expect(paidCard.querySelector(".partner-placement-logo")?.textContent).toBeTruthy();
  expect(title.getAttribute("href")).toBe(placement.url);
});

it.each([undefined, false, true])(
  "opts into sponsored fallback only when explicitly requested (%s)",
  async (allow_sponsored_fallback) => {
    render(
      <PartnerPlacements surface="feed" allow_sponsored_fallback={allow_sponsored_fallback} />,
    );
    await screen.findByRole("link", { name: placement.name });
    expect(JSON.parse(String(vi.mocked(readerRequest).mock.calls[0][1]?.body))).toMatchObject({
      surface: "feed",
      allow_sponsored_fallback: allow_sponsored_fallback === true,
    });
  },
);

it.each(["compact", "card"])(
  "labels every %s product Sponsored with its own tracking receipt and no dismissal control",
  async (layout) => {
    render(
      <PartnerPlacements surface="article" article_id={placement.product_id}>
        {layout === "card" ? (cards) => <div>{cards}</div> : undefined}
      </PartnerPlacements>,
    );
    await screen.findByRole("link", { name: sponsoredPlacement.name });
    expect(screen.getAllByText("Sponsored")).toHaveLength(2);
    expect(screen.queryByRole("button")).toBeNull();
    for (const item of [placement, sponsoredPlacement]) {
      const link = screen.getByRole("link", { name: item.name });
      expect(link.getAttribute("rel")).toBe("nofollow noopener noreferrer");
      expect(link.getAttribute("href")).toBe(item.url);
      if (layout === "card")
        expect(link.querySelector(".partner-placement-destination")?.textContent).toBe("");
      else expect(link.querySelector(".partner-placement-destination")).toBeNull();
      const disclosure = document.getElementById(link.getAttribute("aria-describedby")!);
      expect(disclosure?.textContent).toBe("Sponsored");
      expect(link.closest(".partner-placement")?.contains(disclosure)).toBe(true);
      await waitFor(() =>
        expect(observePartnerPlacement).toHaveBeenCalledWith(
          link,
          item.receipt,
          expect.objectContaining({ completed: expect.any(Set), pending: expect.any(Set) }),
        ),
      );
    }
  },
);

it("keeps viewed products eligible on immediate remount in another context", async () => {
  const mounted = render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await screen.findByRole("link", { name: placement.name });
  await waitFor(() => expect(observePartnerPlacement).toHaveBeenCalled());
  vi.mocked(observePartnerPlacement).mock.calls[0][2]!.completed.add("impression");
  mounted.unmount();
  render(<PartnerPlacements surface="article" article_id={sponsoredPlacement.product_id} />);
  await screen.findByRole("link", { name: placement.name });
  expect(screen.getByRole("link", { name: sponsoredPlacement.name })).toBeDefined();
  const query = JSON.parse(vi.mocked(readerRequest).mock.calls.at(-1)![1]!.body as string);
  expect(query.exclude_product_ids).toEqual([]);
});

it("loads and remounts placements without reading or writing browser storage", async () => {
  const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("storage unavailable");
  });
  const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("storage unavailable");
  });
  const remove = vi.spyOn(Storage.prototype, "removeItem");
  const mounted = render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await screen.findByRole("link", { name: placement.name });
  await waitFor(() => expect(observePartnerPlacement).toHaveBeenCalledTimes(2));
  expect(screen.getByRole("link", { name: sponsoredPlacement.name })).toBeDefined();
  mounted.unmount();
  render(<PartnerPlacements surface="topic" topic_id={placement.product_id} />);
  await screen.findByRole("link", { name: placement.name });
  await waitFor(() => expect(observePartnerPlacement).toHaveBeenCalledTimes(4));
  expect(screen.getByRole("link", { name: sponsoredPlacement.name })).toBeDefined();
  expect(get).not.toHaveBeenCalled();
  expect(set).not.toHaveBeenCalled();
  expect(remove).not.toHaveBeenCalled();
});

it("deduplicates the same product across simultaneously mounted contexts", async () => {
  render(
    <>
      <PartnerPlacements surface="article" article_id={placement.product_id} />
      <PartnerPlacements surface="topic" topic_id={sponsoredPlacement.product_id} />
    </>,
  );
  await screen.findByRole("link", { name: placement.name });
  expect(screen.getAllByRole("link", { name: placement.name })).toHaveLength(1);
});

it("uses the user endpoint and CSRF for personalized delivery and clears products at session change", async () => {
  session.user = { user_id: "reader", csrf_token: "csrf" };
  const mounted = render(<PartnerPlacements surface="feed" />);
  await screen.findByRole("link", { name: placement.name });
  expect(userRequest).toHaveBeenCalledWith(
    "partner-placements/query",
    expect.objectContaining({
      headers: { "Content-Type": "application/json", "X-CSRF-Token": "csrf" },
      cache: "no-store",
    }),
  );
  expect(readerRequest).not.toHaveBeenCalled();
  session.user = null;
  session.loading = true;
  mounted.rerender(<PartnerPlacements surface="feed" />);
  expect(screen.queryByRole("link", { name: placement.name })).toBeNull();
});

it.each([
  { surface: "article" as const, article_id: placement.product_id },
  { surface: "topic" as const, topic_id: placement.product_id },
  { surface: "feed" as const, article_ids: [placement.product_id, sponsoredPlacement.product_id] },
])("uses authenticated context-only delivery on the $surface surface", async (query) => {
  session.user = { user_id: "reader", csrf_token: "private-csrf" };
  render(<PartnerPlacements {...query} />);
  await screen.findByRole("link", { name: placement.name });
  expect(readerRequest).not.toHaveBeenCalled();
  const [path, request] = vi.mocked(userRequest).mock.calls[0];
  expect(path).toBe("partner-placements/query");
  expect(request?.headers).toEqual({
    "Content-Type": "application/json",
    "X-CSRF-Token": "private-csrf",
  });
  expect(JSON.parse(String(request?.body))).toEqual({
    ...query,
    ...(query.surface === "feed" ? { allow_sponsored_fallback: false } : {}),
    exclude_product_ids: [],
  });
});

it("sends the same search intent and visible result identities through authenticated delivery", async () => {
  session.user = { user_id: "reader", csrf_token: "private-csrf" };
  const query = {
    surface: "search" as const,
    search: { query: "TypeScript", results: { topics: [placement.product_id] } },
    ad_formats: ["engagement" as const],
  };
  vi.mocked(userRequest).mockResolvedValue({
    items: [{ ...sponsoredPlacement, ad_format: "engagement" }],
  });
  render(<PartnerPlacements {...query} />);
  await screen.findByRole("link", { name: sponsoredPlacement.name });
  expect(readerRequest).not.toHaveBeenCalled();
  const [path, request] = vi.mocked(userRequest).mock.calls[0];
  expect(path).toBe("partner-placements/query");
  expect(request?.headers).toEqual({
    "Content-Type": "application/json",
    "X-CSRF-Token": "private-csrf",
  });
  expect(JSON.parse(String(request?.body))).toEqual({
    ...query,
    allow_sponsored_fallback: false,
    exclude_product_ids: [],
  });
});

it("waits for session resolution and replaces receipts when authenticated credentials rotate", async () => {
  session.loading = true;
  const mounted = render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await act(async () => {});
  expect(readerRequest).not.toHaveBeenCalled();
  expect(userRequest).not.toHaveBeenCalled();
  session.loading = false;
  session.user = { user_id: "reader", csrf_token: "csrf" };
  session.sessionRevision = 1;
  mounted.rerender(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await screen.findByRole("link", { name: placement.name });
  session.user = { user_id: "reader", csrf_token: "rotated-csrf" };
  mounted.rerender(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await waitFor(() => expect(userRequest).toHaveBeenCalledTimes(2));
  expect(vi.mocked(userRequest).mock.calls[1][1]?.headers).toEqual({
    "Content-Type": "application/json",
    "X-CSRF-Token": "rotated-csrf",
  });
  expect(readerRequest).not.toHaveBeenCalled();
});

it("retains an authenticated delivery after verification returns the same reader and credentials", async () => {
  session.user = { user_id: "reader", csrf_token: "csrf" };
  session.sessionRevision = 1;
  const content = () => (
    <PartnerPlacements surface="feed">{(cards) => <div>{cards}</div>}</PartnerPlacements>
  );
  const mounted = render(content());
  const link = await screen.findByRole("link", { name: placement.name });
  const card = link.closest(".partner-placement")!;
  await waitFor(() => expect(observePartnerPlacement).toHaveBeenCalled());
  const receipt = vi.mocked(observePartnerPlacement).mock.calls[0][1];
  session.loading = true;
  mounted.rerender(content());
  expect(card.getAttribute("data-suspended")).toBeNull();
  expect(card.hasAttribute("inert")).toBe(true);
  session.user = { user_id: "reader", csrf_token: "csrf" };
  session.sessionRevision += 1;
  session.loading = false;
  mounted.rerender(content());
  expect(screen.getByRole("link", { name: placement.name })).toBe(link);
  expect(card.getAttribute("data-suspended")).toBeNull();
  expect(vi.mocked(observePartnerPlacement).mock.calls.at(-1)?.[1]).toBe(receipt);
  expect(userRequest).toHaveBeenCalledTimes(1);
  expect(readerRequest).not.toHaveBeenCalled();
});

it("hides a previous receipt immediately when the same reader's credentials change", async () => {
  session.user = { user_id: "reader", csrf_token: "csrf" };
  const content = () => (
    <PartnerPlacements surface="feed">{(cards) => <div>{cards}</div>}</PartnerPlacements>
  );
  const mounted = render(content());
  await screen.findByRole("link", { name: placement.name });
  const card = mounted.container.querySelector(".partner-placement")!;
  await waitFor(() => expect(observePartnerPlacement).toHaveBeenCalled());
  const previous = vi.mocked(observePartnerPlacement).mock.calls[0];
  let finish!: (value: unknown) => void;
  vi.mocked(userRequest).mockImplementationOnce(() => new Promise((resolve) => (finish = resolve)));
  session.user = { user_id: "reader", csrf_token: "rotated-csrf" };
  mounted.rerender(content());
  expect(card.getAttribute("data-suspended")).toBe("true");
  expect(card.hasAttribute("inert")).toBe(true);
  expect(screen.queryByRole("link", { name: placement.name })).toBeNull();
  const replacement = { ...placement, receipt: partnerReceipt({ assetId: placement.asset_id }) };
  await act(async () => finish({ items: [replacement] }));
  expect(screen.getByRole("link", { name: placement.name })).toBeDefined();
  const current = vi.mocked(observePartnerPlacement).mock.calls.at(-1)!;
  expect(current[1]).toBe(replacement.receipt);
  expect(current[1]).not.toBe(previous[1]);
  expect(current[2]).not.toBe(previous[2]);
  expect(userRequest).toHaveBeenCalledTimes(2);
});

it.each([false, true])(
  "retains visible inert grid cells while verifying the same reader (authenticated: %s)",
  async (authenticated) => {
    session.user = authenticated ? { user_id: "reader", csrf_token: "csrf" } : null;
    const content = () => (
      <PartnerPlacements surface="feed">{(cards) => <div>{cards}</div>}</PartnerPlacements>
    );
    const mounted = render(content());
    await screen.findByRole("link", { name: placement.name });
    const card = mounted.container.querySelector(".partner-placement")!;
    const requests = authenticated ? userRequest : readerRequest;
    expect(requests).toHaveBeenCalledTimes(1);
    session.loading = true;
    mounted.rerender(content());
    expect(mounted.container.querySelector(".partner-placement")).toBe(card);
    expect(card.getAttribute("data-suspended")).toBeNull();
    expect(card.getAttribute("aria-hidden")).toBe("true");
    expect(card.hasAttribute("inert")).toBe(true);
    expect(card.hasAttribute("hidden")).toBe(false);
    expect(screen.queryByRole("link", { name: placement.name })).toBeNull();
    expect(requests).toHaveBeenCalledTimes(1);
    session.loading = false;
    mounted.rerender(content());
    await screen.findByRole("link", { name: placement.name });
    expect(mounted.container.querySelector(".partner-placement")).toBe(card);
    expect(requests).toHaveBeenCalledTimes(1);
  },
);

it("aborts a previous account delivery and cannot show its late response for the next reader", async () => {
  let finish!: (value: unknown) => void;
  vi.mocked(userRequest).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  session.user = { user_id: "first", csrf_token: "first-csrf" };
  const mounted = render(<PartnerPlacements surface="topic" topic_id={placement.product_id} />);
  const oldSignal = vi.mocked(userRequest).mock.calls[0][1]!.signal;
  session.user = { user_id: "second", csrf_token: "second-csrf" };
  session.sessionRevision += 1;
  vi.mocked(userRequest).mockResolvedValueOnce({ items: [sponsoredPlacement] });
  mounted.rerender(<PartnerPlacements surface="topic" topic_id={placement.product_id} />);
  await screen.findByRole("link", { name: sponsoredPlacement.name });
  expect(oldSignal?.aborted).toBe(true);
  await act(async () => finish({ items: [placement] }));
  expect(screen.queryByRole("link", { name: placement.name })).toBeNull();
  session.user = null;
  mounted.rerender(<PartnerPlacements surface="topic" topic_id={placement.product_id} />);
  await waitFor(() => expect(readerRequest).toHaveBeenCalledTimes(1));
  expect(vi.mocked(readerRequest).mock.calls[0][1]?.credentials).toBe("omit");
});

it("gives an article modal its candidates instead of reserving them in a signed-in background feed", async () => {
  session.user = { user_id: "reader", csrf_token: "csrf" };
  prompts.current = { ready: true, modalOpen: false };
  vi.mocked(userRequest).mockResolvedValue({ items: [placement, sponsoredPlacement] });
  const contexts = (
    <>
      <div data-testid="background">
        <PartnerPlacements surface="feed">{(cards) => <div>{cards}</div>}</PartnerPlacements>
      </div>
      <div data-testid="article">
        <PartnerPlacements surface="article" article_id={placement.product_id} />
      </div>
    </>
  );
  const mounted = render(contexts);
  await screen.findByRole("link", { name: placement.name });
  expect(readerRequest).not.toHaveBeenCalled();
  prompts.current = { ready: true, modalOpen: true };
  mounted.rerender(
    <>
      <div data-testid="background">
        <PartnerPlacements surface="feed">{(cards) => <div>{cards}</div>}</PartnerPlacements>
      </div>
      <div data-testid="article">
        <PartnerPlacements surface="article" article_id={placement.product_id} />
      </div>
    </>,
  );
  await waitFor(() =>
    expect(screen.getByTestId("article").querySelectorAll(".partner-placement")).toHaveLength(2),
  );
  expect(
    screen
      .getByTestId("background")
      .querySelector(".partner-placement")
      ?.getAttribute("data-suspended"),
  ).toBeNull();
  expect(userRequest).toHaveBeenCalledTimes(2);
  expect(readerRequest).not.toHaveBeenCalled();
  expect(
    JSON.parse(vi.mocked(userRequest).mock.calls[1][1]!.body as string).exclude_product_ids,
  ).toEqual([]);
  prompts.current = { ready: true, modalOpen: false };
  mounted.rerender(
    <>
      <div data-testid="background">
        <PartnerPlacements surface="feed">{(cards) => <div>{cards}</div>}</PartnerPlacements>
      </div>
      <div data-testid="article">
        <PartnerPlacements surface="article" article_id={placement.product_id} />
      </div>
    </>,
  );
  await waitFor(() =>
    expect(
      screen
        .getByTestId("background")
        .querySelector(".partner-placement")
        ?.getAttribute("data-suspended"),
    ).not.toBe("true"),
  );
  expect(userRequest).toHaveBeenCalledTimes(2);
  expect(readerRequest).not.toHaveBeenCalled();
});

it.each([false, true])(
  "keeps the same feed card and receipt through repeated article previews (authenticated: %s)",
  async (authenticated) => {
    session.user = authenticated ? { user_id: "reader", csrf_token: "csrf" } : null;
    prompts.current = { ready: true, modalOpen: false };
    const stops: ReturnType<typeof vi.fn>[] = [];
    vi.mocked(observePartnerPlacement).mockImplementation(() => {
      const stop = vi.fn();
      stops.push(stop);
      return stop;
    });
    const content = () => (
      <PartnerPlacements surface="feed" article_ids={[placement.product_id]}>
        {(cards) => <div>{cards}</div>}
      </PartnerPlacements>
    );
    const mounted = render(content());
    const link = await screen.findByRole("link", { name: placement.name });
    const card = link.closest(".partner-placement")!;
    const image = card.querySelector("img");
    await waitFor(() => expect(observePartnerPlacement).toHaveBeenCalled());
    const firstTracking = vi.mocked(observePartnerPlacement).mock.calls[0];
    firstTracking[2]!.completed.add("impression");
    for (let index = 0; index < 3; index++) {
      const observed = stops.length;
      prompts.current = { ready: true, modalOpen: true };
      mounted.rerender(content());
      expect(mounted.container.querySelector(".partner-placement")).toBe(card);
      expect(card.querySelector("img")).toBe(image);
      expect(card.getAttribute("data-suspended")).toBeNull();
      expect(card.hasAttribute("hidden")).toBe(false);
      expect(card.getAttribute("aria-hidden")).toBe("true");
      expect(card.hasAttribute("inert")).toBe(true);
      expect(screen.queryByRole("link", { name: placement.name })).toBeNull();
      expect(stops).toHaveLength(observed);
      for (const stop of stops) expect(stop).toHaveBeenCalledTimes(1);
      prompts.current = { ready: true, modalOpen: false };
      mounted.rerender(content());
      expect(screen.getByRole("link", { name: placement.name })).toBe(link);
      expect(card.hasAttribute("inert")).toBe(false);
      expect(vi.mocked(observePartnerPlacement).mock.calls.at(-1)?.[1]).toBeTruthy();
    }
    const sameProduct = vi
      .mocked(observePartnerPlacement)
      .mock.calls.filter((call) => call[0] === link);
    expect(sameProduct).toHaveLength(4);
    for (const call of sameProduct) {
      expect(call[1]).toBe(firstTracking[1]);
      expect(call[2]).toBe(firstTracking[2]);
      expect(call[2]!.completed.has("impression")).toBe(true);
    }
    expect(authenticated ? userRequest : readerRequest).toHaveBeenCalledTimes(1);
    expect(authenticated ? readerRequest : userRequest).not.toHaveBeenCalled();
  },
);

it("retains an in-flight feed selection through an article preview without restarting its request", async () => {
  prompts.current = { ready: true, modalOpen: false };
  let finish!: (response: Response) => void;
  vi.mocked(readerRequest).mockImplementationOnce(
    () => new Promise<Response>((resolve) => (finish = resolve)),
  );
  const content = () => (
    <PartnerPlacements surface="feed">{(cards) => <div>{cards}</div>}</PartnerPlacements>
  );
  const mounted = render(content());
  await waitFor(() => expect(readerRequest).toHaveBeenCalledTimes(1));
  const signal = vi.mocked(readerRequest).mock.calls[0][1]?.signal;
  prompts.current = { ready: true, modalOpen: true };
  mounted.rerender(content());
  expect(signal?.aborted).toBe(false);
  await act(async () => finish(Response.json({ items: [placement] })));
  const card = mounted.container.querySelector(".partner-placement")!;
  expect(card).not.toBeNull();
  expect(card.getAttribute("data-suspended")).toBeNull();
  expect(card.hasAttribute("inert")).toBe(true);
  expect(observePartnerPlacement).not.toHaveBeenCalled();
  prompts.current = { ready: true, modalOpen: false };
  mounted.rerender(content());
  expect(screen.getByRole("link", { name: placement.name }).closest(".partner-placement")).toBe(
    card,
  );
  expect(observePartnerPlacement).toHaveBeenCalledTimes(1);
  expect(readerRequest).toHaveBeenCalledTimes(1);
});

it("does not start a background feed selection until its initial article preview closes", async () => {
  prompts.current = { ready: true, modalOpen: true };
  const content = () => (
    <PartnerPlacements surface="feed">{(cards) => <div>{cards}</div>}</PartnerPlacements>
  );
  const mounted = render(content());
  await act(async () => {});
  expect(readerRequest).not.toHaveBeenCalled();
  prompts.current = { ready: true, modalOpen: false };
  mounted.rerender(content());
  await screen.findByRole("link", { name: placement.name });
  expect(readerRequest).toHaveBeenCalledTimes(1);
});

it("renders nothing for empty, unavailable, or unsafe results and never substitutes random products", async () => {
  vi.mocked(readerRequest).mockResolvedValueOnce(Response.json({ items: [] }));
  const mounted = render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await waitFor(() => expect(readerRequest).toHaveBeenCalledTimes(1));
  expect(mounted.container.innerHTML).toBe("");
  mounted.unmount();
  vi.mocked(readerRequest).mockResolvedValueOnce(
    Response.json({ items: [{ ...placement, url: "javascript:alert(1)" }] }),
  );
  const unsafe = render(<PartnerPlacements surface="article" article_id={placement.product_id} />);
  await act(async () => {});
  expect(unsafe.container.innerHTML).toBe("");
  unsafe.unmount();
  vi.mocked(readerRequest).mockRejectedValueOnce(new Error("offline"));
  const unavailable = render(
    <PartnerPlacements surface="article" article_id={placement.product_id} />,
  );
  await act(async () => {});
  expect(unavailable.container.innerHTML).toBe("");
});
