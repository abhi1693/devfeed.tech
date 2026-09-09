// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { DataTable } from "@/components/molecules/data-table";
import { ApiError } from "@/lib/api/client";
import { loadMatchingRows } from "@/lib/table-selection";

vi.mock("@/lib/notifications", () => ({ notify: { success: vi.fn() }, notifyFailure: vi.fn() }));
afterEach(cleanup);
const rows = [{ id: "a", name: "Alpha" }, { id: "b", name: "Beta" }];
const props = { label: "Example", data: rows, columns: [{ accessorKey: "name", header: "Name" }], getRowId: (row: typeof rows[number]) => row.id, getRowLabel: (row: typeof rows[number]) => row.name, bulkActions: [{ id: "approve", label: "Approve", description: "Apply changes", run: vi.fn() }] };

it("supports checkbox selection, mixed page selection, and explicit clearing", () => {
  render(<DataTable {...props} />);
  fireEvent.click(screen.getByRole("checkbox", { name: "Select Alpha" }));
  const all = screen.getByRole("checkbox", { name: "Select all on this page" }) as HTMLInputElement;
  expect(all.indeterminate).toBe(true);
  expect(screen.getByText("1 selected on this page")).toBeDefined();
  fireEvent.click(all);
  expect(all.checked).toBe(true);
  expect(all.indeterminate).toBe(false);
  expect(screen.getByText("2 selected on this page")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
  expect(all.checked).toBe(false);
  expect(screen.queryByRole("region", { name: "Selected rows" })).toBeNull();
});

it("clears selection and open confirmation when filters change, including when returning to the old filter", () => {
  const run = vi.fn();
  const actions = [{ id: "approve", label: "Approve", description: "Apply changes", run }];
  const view = render(<DataTable {...props} selectionKey="pending" bulkActions={actions} />);
  fireEvent.click(screen.getByRole("checkbox", { name: "Select Alpha" }));
  fireEvent.click(screen.getByRole("button", { name: "Approve" }));
  expect(screen.getByRole("dialog")).toBeDefined();
  view.rerender(<DataTable {...props} selectionKey="approved" bulkActions={actions} />);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByRole("region", { name: "Selected rows" })).toBeNull();
  view.rerender(<DataTable {...props} selectionKey="pending" bulkActions={actions} />);
  expect((screen.getByRole("checkbox", { name: "Select Alpha" }) as HTMLInputElement).checked).toBe(false);
  expect(run).not.toHaveBeenCalled();
});

it("selects only the loaded page and clears selection on page and sort changes", () => {
  const pagination = { offset: 0, limit: 25, total: 1000, onChange: vi.fn() };
  const view = render(<DataTable {...props} pagination={pagination} sort="name" />);
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all on this page" }));
  expect(screen.getByText("2 selected on this page")).toBeDefined();
  view.rerender(<DataTable {...props} pagination={{ ...pagination, offset: 25 }} sort="name" />);
  expect(screen.queryByRole("region", { name: "Selected rows" })).toBeNull();
  fireEvent.click(screen.getByRole("checkbox", { name: "Select Alpha" }));
  view.rerender(<DataTable {...props} pagination={{ ...pagination, offset: 25 }} sort="-name" />);
  expect(screen.queryByRole("region", { name: "Selected rows" })).toBeNull();
});

it("requires typed deletion confirmation and retains failed rows after partial success", async () => {
  const run = vi.fn().mockResolvedValueOnce(undefined).mockRejectedValueOnce(new ApiError(409, "Linked content prevents deletion"));
  const refresh = vi.fn();
  render(<DataTable {...props} onBulkComplete={refresh} bulkActions={[{ id: "delete", label: "Delete", description: "Permanently delete these records.", destructive: true, run }]} />);
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all on this page" }));
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  expect(run).not.toHaveBeenCalled();
  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getByText("Alpha")).toBeDefined();
  expect((screen.getByRole("button", { name: "Delete permanently" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByRole("textbox", { name: "Type DELETE to confirm" }), { target: { value: "DELETE" } });
  fireEvent.click(screen.getByRole("button", { name: "Delete permanently" }));
  await screen.findByText("Linked content prevents deletion");
  expect(run).toHaveBeenCalledTimes(2);
  expect(refresh).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Done" }));
  expect((screen.getByRole("checkbox", { name: "Select Alpha" }) as HTMLInputElement).checked).toBe(false);
  expect((screen.getByRole("checkbox", { name: "Select Beta" }) as HTMLInputElement).checked).toBe(true);
});

it("skips ineligible rows and prevents duplicate submissions while a batch is running", async () => {
  let finish!: () => void;
  const run = vi.fn().mockReturnValue(new Promise<void>(resolve => { finish = resolve; }));
  render(<DataTable {...props} bulkActions={[{ id: "analyze", label: "AI analysis", description: "Queue research", eligible: row => row.id === "a", run }]} />);
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all on this page" }));
  fireEvent.click(screen.getByRole("button", { name: "AI analysis (1)" }));
  expect(screen.getByText("1 ineligible row will be skipped.")).toBeDefined();
  const confirm = screen.getByRole("button", { name: "Confirm ai analysis" });
  fireEvent.click(confirm); fireEvent.click(confirm);
  expect(run).toHaveBeenCalledTimes(1);
  expect((screen.getByRole("button", { name: "Cancel" }) as HTMLButtonElement).disabled).toBe(true);
  finish();
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect((screen.getByRole("checkbox", { name: "Select Beta" }) as HTMLInputElement).checked).toBe(true);
});

it("keeps selection through refreshed rows and submits the snapshot shown in the confirmation", async () => {
  const run = vi.fn().mockResolvedValue(undefined);
  const actions = [{ id: "approve", label: "Approve", description: "Apply changes", run }];
  const view = render(<DataTable {...props} bulkActions={actions} />);
  fireEvent.click(screen.getByRole("checkbox", { name: "Select Alpha" }));
  const updated = [{ ...rows[0], name: "Updated Alpha" }, rows[1]];
  view.rerender(<DataTable {...props} data={updated} bulkActions={actions} />);
  expect((screen.getByRole("checkbox", { name: "Select Updated Alpha" }) as HTMLInputElement).checked).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Approve" }));
  view.rerender(<DataTable {...props} data={rows} bulkActions={actions} />);
  fireEvent.click(screen.getByRole("button", { name: "Confirm approve" }));
  await waitFor(() => expect(run).toHaveBeenCalledWith(updated[0]));
});

it("explicitly expands page selection to all matching records and acts on off-page rows", async () => {
  const allRows = [...rows, { id: "c", name: "Gamma" }];
  const loadAllRows = vi.fn().mockResolvedValue(allRows);
  const run = vi.fn().mockResolvedValue(undefined);
  render(<DataTable {...props} loadAllRows={loadAllRows} pagination={{ offset: 0, limit: 25, total: 3, onChange: vi.fn() }}
    bulkActions={[{ id: "approve", label: "Approve", description: "Apply changes", run }]} />);
  expect(screen.queryByRole("button", { name: "Select all 3 matching records" })).toBeNull();
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all on this page" }));
  expect(loadAllRows).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Select all 3 matching records" }));
  await screen.findByText("3 selected across all pages");
  expect((screen.getByRole("checkbox", { name: "Select all on this page" }) as HTMLInputElement).indeterminate).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Approve" }));
  expect(within(screen.getByRole("dialog")).getByText("Gamma")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Confirm approve" }));
  await waitFor(() => expect(run).toHaveBeenCalledTimes(3));
  expect(run).toHaveBeenCalledWith(allRows[2]);
});

it("cancels selecting all on filter changes and ignores a late response", async () => {
  let complete!: (rows: typeof props.data) => void;
  const loadAllRows = vi.fn().mockReturnValue(new Promise(resolve => { complete = resolve; }));
  const page = { offset: 0, limit: 25, total: 3, onChange: vi.fn() };
  const view = render(<DataTable {...props} loadAllRows={loadAllRows} pagination={page} selectionKey="pending" />);
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all on this page" }));
  fireEvent.click(screen.getByRole("button", { name: "Select all 3 matching records" }));
  expect((screen.getByRole("button", { name: "Approve" }) as HTMLButtonElement).disabled).toBe(true);
  view.rerender(<DataTable {...props} loadAllRows={loadAllRows} pagination={page} selectionKey="approved" />);
  expect(loadAllRows.mock.calls[0][0].aborted).toBe(true);
  complete([...rows, { id: "c", name: "Gamma" }]);
  await waitFor(() => expect(screen.queryByRole("region", { name: "Selected rows" })).toBeNull());
});

it("leaves page selection intact when loading all fails and offers retry", async () => {
  const loadAllRows = vi.fn().mockRejectedValue(new ApiError(503));
  render(<DataTable {...props} loadAllRows={loadAllRows} pagination={{ offset: 0, limit: 25, total: 3, onChange: vi.fn() }} />);
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all on this page" }));
  fireEvent.click(screen.getByRole("button", { name: "Select all 3 matching records" }));
  await screen.findByRole("button", { name: "Select all 3 matching records" });
  expect(screen.getByText("2 selected on this page")).toBeDefined();
  expect(screen.queryByText(/across all pages/)).toBeNull();
});

it("reads every matching page and deduplicates records moved by concurrent edits", async () => {
  const load = vi.fn().mockResolvedValueOnce({ items: rows, total: 4 }).mockResolvedValueOnce({ items: [rows[1], { id: "c", name: "Gamma" }], total: 4 });
  const signal = new AbortController().signal;
  const selected = await loadMatchingRows(load, props.getRowId, signal);
  expect(selected.map(row => row.id)).toEqual(["a", "b", "c"]);
  expect(load).toHaveBeenNthCalledWith(1, 0, 100, signal);
  expect(load).toHaveBeenNthCalledWith(2, 2, 100, signal);
});
