// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AdminSession } from "@/components/molecules/admin-session";
import { LanguageSelect } from "@/components/molecules/language-select";
import { ResourceForm } from "@/components/organisms/resource-form";
import { adminSourcePreview } from "@/lib/api/generated/admin";
import type { SourcePreviewOut } from "@/lib/api/generated/models";
import { ApiError } from "@/lib/api/client";
import { getRecord, saveRecord } from "@/lib/resource-api";
import { toast } from "sonner";

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), refresh: vi.fn() }) }));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));
vi.mock("@/lib/resource-api", async (original) => ({
  ...(await original<typeof import("@/lib/resource-api")>()),
  getRecord: vi.fn(),
  saveRecord: vi.fn(),
}));
vi.mock("@/lib/api/generated/admin", async (original) => ({
  ...(await original<typeof import("@/lib/api/generated/admin")>()),
  adminSourcePreview: vi.fn(),
}));

const details: SourcePreviewOut = {
  name: "Developer News",
  description: "Engineering articles",
  website_url: "https://publication.example/",
  logo_url: "https://publication.example/logo.png",
  image_url: "https://publication.example/cover.png",
  language: "en-us",
  entries_seen: 10,
  entries_skipped: 0,
  warnings: [],
};
const url = "https://publication.example/rss";
const input = (name: string) => screen.getByLabelText(name, { exact: false }) as HTMLInputElement;
const advance = (ms = 650) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
async function mount(id?: string) {
  await act(async () => {
    render(
      <AdminSession
        admin={{
          subject: "admin",
          issuer: "https://identity.example",
          organization_id: "org",
          roles: ["superuser"],
          expires_at: 4102444800,
          csrf_token: "test-csrf",
        }}
      >
        <ResourceForm resource="sources" id={id} />
      </AdminSession>,
    );
  });
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  vi.mocked(adminSourcePreview).mockResolvedValue(details);
  vi.mocked(getRecord).mockResolvedValue({
    id: "source-1",
    ...details,
    feed_url: url,
    source_type: "publisher",
    enabled: true,
    poll_interval_seconds: 1800,
  });
  vi.mocked(saveRecord).mockResolvedValue({ id: "source-1" });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("automatic source details", () => {
  it("waits for a valid URL and a typing pause, without a lookup button or success box", async () => {
    await mount();
    expect(screen.queryByRole("button", { name: "Fetch details" })).toBeNull();
    fireEvent.change(input("RSS / Atom URL"), { target: { value: "https://" } });
    await advance(1000);
    expect(adminSourcePreview).not.toHaveBeenCalled();
    fireEvent.change(input("RSS / Atom URL"), { target: { value: url + "1" } });
    await advance(400);
    fireEvent.change(input("RSS / Atom URL"), { target: { value: url } });
    await advance(649);
    expect(adminSourcePreview).not.toHaveBeenCalled();
    await advance(1);
    expect(adminSourcePreview).toHaveBeenCalledWith(
      { feed_url: url, source_type: "publisher" },
      { signal: expect.any(AbortSignal), headers: { "X-CSRF-Token": "test-csrf" } },
    );
    expect(input("Name").value).toBe("Developer News");
    expect(input("Website").value).toBe(details.website_url);
    expect(screen.getByRole("combobox", { name: "Language" }).textContent).toBe(
      "English (United States)",
    );
    expect(screen.queryByText(/Feed validated/)).toBeNull();
    expect(screen.queryByText(/Nothing has been saved/)).toBeNull();
    expect(saveRecord).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();
    expect(toast.info).not.toHaveBeenCalled();
    await advance(2000);
    expect(adminSourcePreview).toHaveBeenCalledTimes(1);
  });

  it("preserves edits made before or during lookup and disables creation while pending", async () => {
    let resolve!: (value: SourcePreviewOut) => void;
    vi.mocked(adminSourcePreview).mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    await mount();
    fireEvent.change(input("Name"), { target: { value: "My name" } });
    fireEvent.change(input("RSS / Atom URL"), { target: { value: url } });
    await advance();
    expect(
      (screen.getByRole("button", { name: "Create source" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "Create and add another" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    fireEvent.change(input("Short description"), { target: { value: "My description" } });
    fireEvent.click(screen.getByRole("combobox", { name: "Language" }));
    fireEvent.change(screen.getByPlaceholderText("Search languages or codes…"), {
      target: { value: "French" },
    });
    fireEvent.click(screen.getByRole("option", { name: "French fr" }));
    await act(async () => {
      resolve(details);
    });
    expect(input("Name").value).toBe("My name");
    expect(input("Short description").value).toBe("My description");
    expect(screen.getByRole("combobox", { name: "Language" }).textContent).toBe("French");
    expect(
      (screen.getByRole("button", { name: "Create source" }) as HTMLButtonElement).disabled,
    ).toBe(false);
    expect(
      (screen.getByRole("button", { name: "Create and add another" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  });

  it("starts the next source with clean fields, defaults, and a fresh metadata lookup", async () => {
    await mount();
    fireEvent.change(input("RSS / Atom URL"), { target: { value: url } });
    await advance();
    fireEvent.change(input("Name"), { target: { value: "Custom first feed" } });
    fireEvent.change(input("Poll interval"), { target: { value: "900" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Create and add another" }));
    });
    expect(saveRecord).toHaveBeenCalledWith(
      "sources",
      expect.objectContaining({
        feed_url: url,
        name: "Custom first feed",
        poll_interval_seconds: 900,
      }),
      "test-csrf",
      undefined,
    );
    for (const field of [
      "RSS / Atom URL",
      "Name",
      "Website",
      "Short description",
      "Logo URL",
      "Image URL",
    ])
      expect(input(field).value).toBe("");
    expect(input("Poll interval").value).toBe("1800");
    expect(input("Enable polling").checked).toBe(true);
    expect(document.activeElement).toBe(input("RSS / Atom URL"));
    vi.mocked(adminSourcePreview).mockResolvedValueOnce({
      ...details,
      name: "Second feed",
      website_url: "https://second.example/",
    });
    fireEvent.change(input("RSS / Atom URL"), { target: { value: "https://second.example/rss" } });
    await advance();
    expect(adminSourcePreview).toHaveBeenCalledTimes(2);
    expect(input("Name").value).toBe("Second feed");
    expect(input("Website").value).toBe("https://second.example/");
  });

  it("aborts old requests and ignores their late responses", async () => {
    let resolve!: (value: SourcePreviewOut) => void;
    vi.mocked(adminSourcePreview).mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    vi.mocked(adminSourcePreview).mockResolvedValueOnce({ ...details, name: "Second feed" });
    await mount();
    fireEvent.change(input("RSS / Atom URL"), { target: { value: url } });
    await advance();
    const signal = vi.mocked(adminSourcePreview).mock.calls[0][1]?.signal;
    fireEvent.change(input("RSS / Atom URL"), { target: { value: "https://second.example/rss" } });
    expect(signal?.aborted).toBe(true);
    await advance();
    await act(async () => {
      resolve(details);
    });
    expect(input("Name").value).toBe("Second feed");
  });

  it("clears old automatic metadata but preserves manual edits when the URL changes", async () => {
    await mount();
    fireEvent.change(input("RSS / Atom URL"), { target: { value: url } });
    await advance();
    fireEvent.change(input("Name"), { target: { value: "My name" } });
    fireEvent.change(input("RSS / Atom URL"), { target: { value: "" } });
    expect(input("Name").value).toBe("My name");
    expect(input("Website").value).toBe("");
    fireEvent.change(input("RSS / Atom URL"), { target: { value: url } });
    await advance();
    expect(adminSourcePreview).toHaveBeenCalledTimes(2);
    expect(input("Website").value).toBe(details.website_url);
  });

  it("shows duplicate URL errors inline, without retry loops or erasing them on unrelated edits", async () => {
    const message =
      "A source with this RSS / Atom URL already exists. Edit the existing source instead.";
    vi.mocked(adminSourcePreview).mockRejectedValueOnce(
      new ApiError(409, "Please correct the highlighted fields.", { feed_url: message }),
    );
    await mount();
    fireEvent.change(input("RSS / Atom URL"), { target: { value: url } });
    await advance();
    expect(screen.getByText(message)).toBeDefined();
    expect(input("RSS / Atom URL").getAttribute("aria-invalid")).toBe("true");
    fireEvent.change(input("Name"), { target: { value: "Anything" } });
    await advance(2000);
    expect(screen.getByText(message)).toBeDefined();
    expect(adminSourcePreview).toHaveBeenCalledTimes(1);
    fireEvent.change(input("RSS / Atom URL"), { target: { value: "https://second.example/rss" } });
    await advance();
    expect(screen.queryByText(message)).toBeNull();
  });

  it("toasts actionable partial-lookup warnings without adding a banner", async () => {
    vi.mocked(adminSourcePreview).mockResolvedValueOnce({
      ...details,
      warnings: ["Website unavailable; fill missing details manually."],
    });
    await mount();
    fireEvent.change(input("RSS / Atom URL"), { target: { value: url } });
    await advance();
    expect(toast.warning).toHaveBeenCalledWith(
      "Some source details could not be fetched",
      expect.objectContaining({
        description: "Website unavailable; fill missing details manually.",
      }),
    );
    expect(screen.queryByText("Website unavailable; fill missing details manually.")).toBeNull();
    expect(input("Name").value).toBe("Developer News");
  });

  it("does not refetch or reject an existing source when opening its edit form", async () => {
    await mount("source-1");
    await advance(2000);
    expect(adminSourcePreview).not.toHaveBeenCalled();
    expect(input("RSS / Atom URL").disabled).toBe(true);
    expect(input("Name").value).toBe("Developer News");
  });
});

describe("language selector", () => {
  it("shows readable names and preserves regional and three-letter codes", () => {
    const changed = vi.fn();
    const view = render(
      <LanguageSelect aria-label="Language" value="sr-latn-rs" onChange={changed} />,
    );
    const select = screen.getByRole("combobox", { name: "Language" });
    expect(select.textContent).toContain("Serbian");
    fireEvent.click(select);
    fireEvent.change(screen.getByPlaceholderText("Search languages or codes…"), {
      target: { value: "Hindi" },
    });
    fireEvent.click(screen.getByRole("option", { name: "Hindi hi" }));
    expect(changed).toHaveBeenCalledWith("hi");
    view.rerender(<LanguageSelect aria-label="Language" value="yue" onChange={changed} />);
    expect(select.textContent).toBe("Cantonese");
    fireEvent.click(select);
    expect(screen.getByRole("option", { name: "Unknown / not specified" })).toBeDefined();
  });
});
