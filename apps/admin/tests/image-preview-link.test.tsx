// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ImagePreviewLink } from "@/components/molecules/image-preview-link";
import { ResourceDetail } from "@/components/organisms/resource-detail";
import { getRecord } from "@/lib/resource-api";

vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams() }));
vi.mock("@/lib/resource-api", async original => ({ ...await original<typeof import("@/lib/resource-api")>(), getRecord: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const logo = "https://publication.example/logo.png?fit=32%2C32";
const cover = "https://publication.example/cover.png?fit=1201%2C630";

describe("clickable read-only image previews", () => {
  it.each(["logo", "image"] as const)("renders a %s preview that opens the original URL safely in a new tab", kind => {
    render(<ImagePreviewLink value={cover} kind={kind} />);
    const link = screen.getByRole("link", { name: `Open ${kind} in a new tab` });
    expect(link.getAttribute("href")).toBe(cover);
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(link.getAttribute("referrerpolicy")).toBe("no-referrer");
    const img = screen.getByAltText(kind === "logo" ? "Logo preview" : "Image preview");
    expect(img.getAttribute("src")).toBe(cover);
    expect(img.getAttribute("loading")).toBe("lazy");
    expect(img.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(screen.queryByText(cover)).toBeNull();
    fireEvent.load(img);
    expect(screen.queryByRole("status")).toBeNull();
    expect(img.className).not.toContain("invisible");
    link.focus();
    expect(document.activeElement).toBe(link);
  });

  it("keeps a failed preview clickable and resets its fallback when the URL changes", () => {
    const { rerender } = render(<ImagePreviewLink value={logo} kind="logo" />);
    fireEvent.error(screen.getByAltText("Logo preview"));
    expect(screen.getByRole("status", { name: "Preview unavailable" })).toBeTruthy();
    expect(screen.getByRole("link").getAttribute("href")).toBe(logo);
    expect(screen.queryByRole("img")).toBeNull();
    rerender(<ImagePreviewLink value={cover} kind="logo" />);
    expect(screen.queryByRole("status", { name: "Preview unavailable" })).toBeNull();
    expect(screen.getByAltText("Logo preview").getAttribute("src")).toBe(cover);
    expect(screen.getByRole("link").getAttribute("href")).toBe(cover);
  });

  it.each([null, undefined, ""])("keeps missing metadata as a placeholder: %j", value => {
    render(<ImagePreviewLink value={value} />);
    expect(screen.getByText("—")).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
    expect(document.querySelector("img")).toBeNull();
  });

  it.each(["javascript:alert(1)", "data:image/svg+xml,bad", "https://user:secret@publication.example/image.png", "not a url"])("does not create unsafe links or image requests: %s", value => {
    render(<ImagePreviewLink value={value} />);
    expect(screen.getByText("Preview unavailable")).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
    expect(document.querySelector("img")).toBeNull();
  });

  it.each(["sources", "topics", "articles"] as const)("uses previews for media fields on real %s detail screens, leaving ordinary URLs as links", async resource => {
    vi.mocked(getRecord).mockResolvedValue({ id: "record-1", name: "Publisher", title: "Article", logo_url: logo, image_url: cover, website_url: "https://publication.example/", canonical_url: "https://publication.example/article", publication_blockers: [], topics: [], tags: [], sources: [] });
    render(<ResourceDetail resource={resource} id="record-1" />);
    await screen.findByRole("group", { name: "Record actions" });
    if (resource !== "articles") {
      const logoLink = screen.getByRole("link", { name: "Open logo in a new tab" });
      const heading = screen.getByRole("heading", { level: 1, name: "Publisher" });
      expect(logoLink.getAttribute("href")).toBe(logo);
      expect(heading.parentElement!.parentElement!.contains(logoLink)).toBe(true);
      expect(logoLink.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(screen.getByAltText("Logo preview").getAttribute("loading")).toBe("eager");
      expect(screen.getByAltText("Logo preview").parentElement!.className).toContain("size-8");
    } else expect(screen.queryByRole("link", { name: "Open logo in a new tab" })).toBeNull();
    expect(screen.queryByText("Logo URL")).toBeNull();
    if (resource !== "topics") expect(screen.getByRole("link", { name: "Open image in a new tab" }).getAttribute("href")).toBe(cover);
    const ordinaryUrl = resource === "articles" ? "https://publication.example/article" : "https://publication.example/";
    expect(screen.getByRole("link", { name: ordinaryUrl }).getAttribute("href")).toBe(ordinaryUrl);
    expect(screen.queryByText(logo)).toBeNull();
    expect(screen.queryByText(cover)).toBeNull();
  });

  it.each([null, "", "javascript:alert(1)"])("omits absent or unsafe header logos without adding an empty details row: %j", async value => {
    vi.mocked(getRecord).mockResolvedValue({ id: "record-1", name: "Cloudflare AI", logo_url: value });
    render(<ResourceDetail resource="sources" id="record-1" />);
    await screen.findByRole("heading", { level: 1, name: "Cloudflare AI" });
    expect(screen.queryByRole("link", { name: "Open logo in a new tab" })).toBeNull();
    expect(screen.queryByText("Logo URL")).toBeNull();
    expect(screen.queryByAltText("Logo preview")).toBeNull();
  });

  it("keeps a failed header logo compact, clickable and separate from the heading text", async () => {
    vi.mocked(getRecord).mockResolvedValue({ id: "record-1", name: "Cloudflare AI", logo_url: logo });
    render(<ResourceDetail resource="sources" id="record-1" />);
    await screen.findByRole("heading", { level: 1, name: "Cloudflare AI" });
    fireEvent.error(screen.getByAltText("Logo preview"));
    expect(screen.getByRole("status", { name: "Preview unavailable" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open logo in a new tab" }).getAttribute("href")).toBe(logo);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Cloudflare AI");
  });
});
