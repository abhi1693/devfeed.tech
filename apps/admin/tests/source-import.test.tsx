// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SourceImport } from "@/components/organisms/source-import";
import { AdminSession } from "@/components/molecules/admin-session";
import { adminSourceImportSubmit } from "@/lib/api/generated/admin";
import { resolveAdminRoute } from "@/lib/routes";

vi.mock("@/lib/api/generated/admin", () => ({
  adminSourceImportSubmit: vi.fn().mockResolvedValue({ created: 2, existing: 1, total: 3 }),
  adminSourceImportCandidates: vi.fn(),
  adminSourceImportDelete: vi.fn().mockResolvedValue({ deleted: 1 }),
  adminSourceImportCandidate: vi.fn(),
  adminSourceImportReview: vi.fn(),
}));

vi.mock("@/components/molecules/select", () => ({
  Select: ({
    label,
    options,
    value,
    onChange,
  }: {
    label: string;
    options: { value: string; label: string }[];
    value: string;
    onChange: (value: string) => void;
  }) => (
    <select aria-label={label} value={value} onChange={(event) => onChange(event.target.value)}>
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  ),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
function mount() {
  render(
    <AdminSession
      admin={{
        subject: "tester",
        issuer: "https://id.example",
        organization_id: "org",
        roles: ["superuser"],
        expires_at: 4102444800,
        csrf_token: "test-csrf",
      }}
    >
      <SourceImport />
    </AdminSession>,
  );
}

it("links sources to import and candidate review routes", () => {
  expect(resolveAdminRoute(["content", "sources", "import"])).toEqual({ view: "source-import" });
  expect(resolveAdminRoute(["content", "sources", "imports", "candidate-id"])).toEqual({
    view: "source-import-review",
    id: "candidate-id",
  });
});
it("submits a collection URL without a vetting override or approval flag", async () => {
  mount();
  fireEvent.change(screen.getByLabelText("Collection URL", { exact: false }), {
    target: { value: "https://collection.example/feeds.opml" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Import for review" }));
  await screen.findByText(/2 new publishers imported/);
  expect(adminSourceImportSubmit).toHaveBeenCalledWith(
    expect.objectContaining({
      url: "https://collection.example/feeds.opml",
      format: "opml",
    }),
    { headers: { "X-CSRF-Token": "test-csrf" } },
  );
  expect(vi.mocked(adminSourceImportSubmit).mock.calls[0][0]).not.toHaveProperty("approval_status");
  expect(screen.queryByRole("combobox", { name: "Vetting method" })).toBeNull();
  expect(vi.mocked(adminSourceImportSubmit).mock.calls[0][0]).not.toHaveProperty("review_method");
  expect(screen.queryByText(/Sources are reviewed with AI/)).toBeNull();
});
it("supports pasted URL lists using the system vetting mode", async () => {
  mount();
  fireEvent.change(screen.getByRole("combobox", { name: "Import from" }), {
    target: { value: "paste" },
  });
  fireEvent.change(screen.getByRole("combobox", { name: "Format" }), { target: { value: "urls" } });
  fireEvent.change(screen.getByLabelText("Collection content", { exact: false }), {
    target: { value: "https://publisher.example/blog" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Import for review" }));
  await waitFor(() =>
    expect(adminSourceImportSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        content: "https://publisher.example/blog",
        format: "urls",
      }),
      expect.anything(),
    ),
  );
});
it("reads an uploaded collection and infers its format", async () => {
  mount();
  fireEvent.change(screen.getByRole("combobox", { name: "Import from" }), {
    target: { value: "file" },
  });
  const file = new File(['[{"homepage_url":"https://publisher.example/"}]'], "publishers.json", {
    type: "application/json",
  });
  Object.defineProperty(file, "text", {
    value: async () => '[{"homepage_url":"https://publisher.example/"}]',
  });
  fireEvent.change(screen.getByLabelText("Collection file", { exact: false }), {
    target: { files: [file] },
  });
  await waitFor(() =>
    expect((screen.getByRole("combobox", { name: "Format" }) as HTMLSelectElement).value).toBe(
      "json",
    ),
  );
  fireEvent.submit(screen.getByRole("button", { name: "Import for review" }).closest("form")!);
  await waitFor(() =>
    expect(adminSourceImportSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ name: "publishers.json", format: "json" }),
      expect.anything(),
    ),
  );
});

it("returns to the common sources queue without an imported publishers table", () => {
  mount();
  expect(screen.getByRole("link", { name: "View pending sources" }).getAttribute("href")).toBe(
    "/content/sources?approval_status=pending",
  );
  expect(screen.queryByRole("table")).toBeNull();
});
