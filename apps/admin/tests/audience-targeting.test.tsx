// @vitest-environment jsdom
import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import {
  AudienceTargeting,
  audienceValue,
  type Audience,
  type AudienceTopicLoader,
} from "@devfeed/ui/audience-targeting";

const rust = { id: "rust", name: "Rust", slug: "rust", kind: "language", status: "active" };
const docker = { id: "docker", name: "Docker", slug: "docker", kind: "tool", status: "active" };
function Editor({
  loader,
  initial = audienceValue(),
}: {
  loader: AudienceTopicLoader;
  initial?: Audience;
}) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <AudienceTargeting value={value} onChange={setValue} loadTopics={loader} />
      <output data-testid="value">{JSON.stringify(value)}</output>
    </>
  );
}
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it("does not download catalogs until opened and keeps independent Any selection groups", async () => {
  const loader = vi.fn<AudienceTopicLoader>().mockImplementation(async (dimension) => ({
    items: [dimension === "languages" ? rust : docker],
    total: 1,
  }));
  render(<Editor loader={loader} />);
  expect(loader).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("checkbox", { name: "Senior" }));
  await userEvent.click(screen.getByRole("combobox", { name: "Programming languages" }));
  await userEvent.click(await screen.findByRole("option", { name: /Rust/ }));
  await userEvent.click(screen.getByRole("combobox", { name: "Developer tools" }));
  await userEvent.click(await screen.findByRole("option", { name: /Docker/ }));
  expect(JSON.parse(screen.getByTestId("value").textContent!)).toEqual({
    experience_levels: ["senior"],
    language_topic_ids: ["rust"],
    tool_topic_ids: ["docker"],
  });
  fireEvent.click(screen.getByRole("button", { name: "Any programming languages" }));
  expect(JSON.parse(screen.getByTestId("value").textContent!).language_topic_ids).toEqual([]);
  expect(screen.getByRole("list", { name: "Selected developer tools" }).textContent).toContain(
    "Docker",
  );
});

it("debounces search on the server, cancels stale requests and paginates the matching catalog", async () => {
  const loader = vi.fn<AudienceTopicLoader>().mockImplementation(async (_dimension, params) => ({
    items: params.offset ? [rust] : [docker],
    total: 26,
  }));
  render(<Editor loader={loader} />);
  fireEvent.click(screen.getByRole("combobox", { name: "Developer tools" }));
  await screen.findByRole("option", { name: /Docker/ });
  const firstSignal = loader.mock.calls[0][2];
  fireEvent.click(screen.getByRole("button", { name: "Next developer tools" }));
  await screen.findByRole("option", { name: /Rust/ });
  expect(firstSignal.aborted).toBe(true);
  expect(loader).toHaveBeenLastCalledWith(
    "tools",
    { q: "", offset: 25, limit: 25 },
    expect.any(AbortSignal),
  );
  const search = screen.getByPlaceholderText("Search developer tools…");
  vi.useFakeTimers();
  fireEvent.change(search, { target: { value: "  kubernetes  " } });
  expect(loader).toHaveBeenCalledTimes(2);
  await act(() => vi.advanceTimersByTimeAsync(250));
  expect(loader).toHaveBeenLastCalledWith(
    "tools",
    { q: "kubernetes", offset: 0, limit: 25 },
    expect.any(AbortSignal),
  );
});

it("resolves saved names with exact bounded requests and warns about retired selections", async () => {
  const loader = vi
    .fn<AudienceTopicLoader>()
    .mockResolvedValue({ items: [{ ...rust, status: "retired" }], total: 1 });
  render(
    <AudienceTargeting
      value={audienceValue({ language_topic_ids: ["rust"] })}
      loadTopics={loader}
    />,
  );
  const selected = await screen.findByRole("list", { name: "Selected programming languages" });
  await within(selected).findByText("Rust");
  expect(within(selected).getByText("(retired)")).toBeTruthy();
  expect(loader).toHaveBeenCalledExactlyOnceWith(
    "languages",
    { topic_id: "rust", limit: 1 },
    expect.any(AbortSignal),
  );
  expect(screen.queryByRole("combobox")).toBeNull();
});

it("retries catalog failures without losing the current selection", async () => {
  const loader = vi
    .fn<AudienceTopicLoader>()
    .mockRejectedValueOnce(new Error("network"))
    .mockResolvedValue({ items: [rust], total: 1 });
  render(<Editor loader={loader} initial={audienceValue({ experience_levels: ["senior"] })} />);
  fireEvent.click(screen.getByRole("combobox", { name: "Programming languages" }));
  await screen.findByText("Could not load options.");
  fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
  await screen.findByRole("option", { name: /Rust/ });
  expect((screen.getByRole("checkbox", { name: "Senior" }) as HTMLInputElement).checked).toBe(true);
});

it("shows unresolved saved criteria without broadening them and allows explicit name retry", async () => {
  const loader = vi
    .fn<AudienceTopicLoader>()
    .mockResolvedValueOnce({ items: [], total: 0 })
    .mockResolvedValue({ items: [rust], total: 1 });
  render(<Editor loader={loader} initial={audienceValue({ language_topic_ids: ["rust"] })} />);
  await screen.findByText("Unavailable selection");
  expect(JSON.parse(screen.getByTestId("value").textContent!).language_topic_ids).toEqual(["rust"]);
  fireEvent.click(screen.getByRole("button", { name: "Retry names" }));
  await screen.findByText("Rust");
});

it("aborts selected-name and catalog requests when leaving the editor", async () => {
  const loader = vi.fn<AudienceTopicLoader>().mockImplementation(() => new Promise(() => {}));
  const view = render(
    <Editor loader={loader} initial={audienceValue({ language_topic_ids: ["rust"] })} />,
  );
  fireEvent.click(screen.getByRole("combobox", { name: "Developer tools" }));
  await waitFor(() => expect(loader).toHaveBeenCalledTimes(2));
  view.unmount();
  expect(loader.mock.calls.every((call) => call[2].aborted)).toBe(true);
});
