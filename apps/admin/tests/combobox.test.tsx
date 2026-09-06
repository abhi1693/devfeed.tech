// @vitest-environment jsdom
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Combobox } from "@/components/molecules/combobox";
import { EntityPicker } from "@/components/molecules/entity-picker";
import { LanguageSelect } from "@/components/molecules/language-select";
import { getRecord, listRecords } from "@/lib/resource-api";
import { ApiError } from "@/lib/api/client";

vi.mock("@/lib/resource-api", () => ({ getRecord: vi.fn(), listRecords: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() } }));
const options = [{ value: "react", label: "React", description: "UI library", keywords: ["reactjs"] }, { value: "angular", label: "Angular", description: "Application framework" }];
function Example({ required = false, disabled = false, onSubmit = vi.fn() }: { required?: boolean; disabled?: boolean; onSubmit?: (value: FormData) => void }) {
  const [value, setValue] = useState("");
  return <form onSubmit={event => { event.preventDefault(); onSubmit(new FormData(event.currentTarget)); }}>
    <Combobox label="Technology" id="technology" name="technology" value={value} onChange={setValue} required={required} disabled={disabled} options={options} />
    <button type="submit">Save</button>
  </form>;
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getRecord).mockResolvedValue({ id: "selected", name: "Selected topic", slug: "selected" });
  vi.mocked(listRecords).mockResolvedValue({ items: [{ id: "react", name: "React", slug: "react", kind: "framework", status: "active" }], total: 1, offset: 0, limit: 25 });
});
afterEach(cleanup);

describe("searchable combobox", () => {
  it("shows a pointer cursor on hover but opens only on click", async () => {
    const user = userEvent.setup(); render(<Example />);
    const save = screen.getByRole("button", { name: "Save" }); save.focus();
    const trigger = screen.getByRole("combobox", { name: "Technology" });
    await user.hover(trigger);
    await act(async () => { await new Promise(done => setTimeout(done, 300)); });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(save);
    expect(trigger.className).toContain("cursor-pointer");
    await user.click(trigger);
    expect(document.activeElement).toBe(screen.getByPlaceholderText("Search technology…"));
  });
  it("opens a focused search, filters aliases, selects and restores trigger focus", async () => {
    const user = userEvent.setup(); render(<Example />);
    const trigger = screen.getByRole("combobox", { name: "Technology" });
    await user.click(trigger);
    const search = screen.getByPlaceholderText("Search technology…");
    expect(document.activeElement).toBe(search);
    await user.type(search, "reactjs");
    expect(screen.queryByRole("option", { name: /Angular/ })).toBeNull();
    await user.click(screen.getByRole("option", { name: /React/ }));
    expect(trigger.textContent).toBe("React");
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });
  it("supports arrow keys, Enter and Escape without submitting the parent form", async () => {
    const user = userEvent.setup(); const saved = vi.fn(); render(<Example onSubmit={saved} />);
    const trigger = screen.getByRole("combobox", { name: "Technology" }); trigger.focus();
    await user.keyboard("{ArrowDown}");
    await user.type(screen.getByPlaceholderText("Search technology…"), "Angular");
    await user.keyboard("{ArrowDown}{Enter}");
    expect(trigger.textContent).toBe("Angular"); expect(saved).not.toHaveBeenCalled();
    await user.click(trigger); await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(trigger.textContent).toBe("Angular");
  });
  it("reports no matches and resets the query when reopened", async () => {
    const user = userEvent.setup(); render(<Example />);
    const trigger = screen.getByRole("combobox", { name: "Technology" });
    await user.click(trigger); await user.type(screen.getByPlaceholderText("Search technology…"), "nonexistent");
    expect(screen.getByText("No results found.")).toBeDefined();
    await user.keyboard("{Escape}"); await user.click(trigger);
    expect((screen.getByPlaceholderText("Search technology…") as HTMLInputElement).value).toBe("");
    expect(screen.getByRole("option", { name: /Angular/ })).toBeDefined();
  });
  it("clears optional values explicitly and preserves FormData", async () => {
    const user = userEvent.setup(); const saved = vi.fn(); render(<Example onSubmit={saved} />);
    const trigger = screen.getByRole("combobox", { name: "Technology" });
    await user.click(trigger); await user.click(screen.getByRole("option", { name: /React/ }));
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(saved.mock.lastCall?.[0].get("technology")).toBe("react");
    await user.click(trigger); await user.click(screen.getByRole("option", { name: "None" }));
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(saved.mock.lastCall?.[0].get("technology")).toBe("");
  });
  it("blocks required empty values and exposes a focusable, labelled validation error", async () => {
    const user = userEvent.setup(); const saved = vi.fn(); render(<Example required onSubmit={saved} />);
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(saved).not.toHaveBeenCalled();
    const trigger = screen.getByRole("combobox", { name: "Technology" });
    expect(trigger.getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByRole("alert").textContent).toBe("Select technology.");
    expect(screen.queryByRole("option", { name: "None" })).toBeNull();
    await user.click(screen.getByRole("option", { name: /React/ }));
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(saved).toHaveBeenCalledOnce();
  });
  it("respects disabled controls and inherited disabled fieldsets", async () => {
    const user = userEvent.setup(); const view = render(<Example disabled />);
    await user.click(screen.getByRole("combobox", { name: "Technology" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    view.rerender(<fieldset disabled><Example /></fieldset>);
    await user.click(screen.getByRole("combobox", { name: "Technology" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
  it("retains selected values that are absent from the current choices", () => {
    render(<Combobox label="Topic" value="old" onChange={vi.fn()} options={[]} selectedOption={{ value: "old", label: "Current topic" }} />);
    expect(screen.getByRole("combobox", { name: "Topic" }).textContent).toBe("Current topic");
  });
  it("searches language names and codes without dropping an uncommon selected tag", async () => {
    const user = userEvent.setup(); const changed = vi.fn();
    render(<LanguageSelect value="yue" onChange={changed} />);
    const trigger = screen.getByRole("combobox", { name: "Language" });
    expect(trigger.textContent).toBe("Cantonese");
    await user.click(trigger); await user.type(screen.getByPlaceholderText("Search languages or codes…"), "en-us");
    await user.click(screen.getByRole("option", { name: "English (United States) en-us" }));
    expect(changed).toHaveBeenCalledWith("en-us");
  });
});

describe("API-backed choices", () => {
  it("loads only on open, keeps selected labels and searches the API after a typing pause", async () => {
    const user = userEvent.setup(); render(<EntityPicker id="topic" label="Topic" resource="topics" value="selected" onChange={vi.fn()} />);
    const trigger = screen.getByRole("combobox", { name: "Topic" });
    await waitFor(() => expect(trigger.textContent).toBe("Selected topic"));
    expect(listRecords).not.toHaveBeenCalled();
    await user.click(trigger); await screen.findByRole("option", { name: /React/ });
    const search = screen.getByPlaceholderText("Search topics…");
    fireEvent.change(search, { target: { value: "terraform" } });
    expect(screen.queryByRole("option", { name: /React/ })).toBeNull();
    expect(listRecords).toHaveBeenCalledTimes(1);
    vi.mocked(listRecords).mockResolvedValue({ items: [{ id: "match", name: "Matched by an alias" }], total: 1, limit: 25, offset: 0 });
    await screen.findByRole("option", { name: "Matched by an alias" });
    expect(listRecords).toHaveBeenLastCalledWith("topics", { q: "terraform", offset: 0, limit: 25 }, expect.any(AbortSignal));
    expect(trigger.textContent).toBe("Selected topic");
  });
  it("paginates within the dropdown and resets to the first page for a new search", async () => {
    const user = userEvent.setup();
    vi.mocked(listRecords).mockResolvedValue({ items: [{ id: "react", name: "React" }], total: 60, limit: 25, offset: 0 });
    render(<EntityPicker id="topic" label="Topic" resource="topics" value="" onChange={vi.fn()} />);
    await user.click(screen.getByRole("combobox", { name: "Topic" }));
    const next = await screen.findByRole("button", { name: "Next options" });
    // Enter on pagination must paginate, never select the highlighted option.
    next.focus(); await user.keyboard("{Enter}");
    await waitFor(() => expect(listRecords).toHaveBeenLastCalledWith("topics", { q: "", offset: 25, limit: 25 }, expect.any(AbortSignal)));
    fireEvent.change(screen.getByPlaceholderText("Search topics…"), { target: { value: "React" } });
    await waitFor(() => expect(listRecords).toHaveBeenLastCalledWith("topics", { q: "React", offset: 0, limit: 25 }, expect.any(AbortSignal)));
    expect(screen.getByRole("dialog")).toBeDefined();
  });
  it("excludes self-links, explains empty matches and can retry failed lookups", async () => {
    const user = userEvent.setup(); vi.mocked(listRecords).mockRejectedValueOnce(new ApiError(503));
    render(<EntityPicker id="topic" label="Topic" resource="topics" value="" onChange={vi.fn()} exclude="react" />);
    await user.click(screen.getByRole("combobox", { name: "Topic" }));
    const retry = await screen.findByRole("button", { name: "Retry" });
    retry.focus(); await user.keyboard("{Enter}");
    await screen.findByText("No matching topics.");
    expect(screen.queryByRole("option", { name: /React/ })).toBeNull();
    expect(listRecords).toHaveBeenCalledTimes(2);
  });
  it("discards late results after closing the dropdown", async () => {
    const user = userEvent.setup(); let resolve!: (value: Awaited<ReturnType<typeof listRecords>>) => void;
    vi.mocked(listRecords).mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    render(<EntityPicker id="topic" label="Topic" resource="topics" value="" onChange={vi.fn()} />);
    await user.click(screen.getByRole("combobox", { name: "Topic" }));
    const signal = vi.mocked(listRecords).mock.calls[0][2]!;
    await user.keyboard("{Escape}");
    expect(signal.aborted).toBe(true);
    await act(async () => resolve({ items: [{ id: "late", name: "Late topic" }], total: 1, offset: 0, limit: 25 }));
    expect(screen.queryByText("Late topic")).toBeNull();
  });
});
