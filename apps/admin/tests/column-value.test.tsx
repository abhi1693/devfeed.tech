// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { DataTable } from "@/components/molecules/data-table";
import { ColumnValue } from "@/components/molecules/column-value";
import Link from "next/link";

afterEach(cleanup);

it("renders explicit column kinds without altering stored names and slugs", () => {
  const rows = [
    {
      id: "one",
      name: "c/C++",
      slug: "c-c-plus-plus",
      enabled: false,
      tags: ["C#", { name: ".NET" }],
      status: "approved",
      created: "2026-09-12T12:00:00Z",
    },
  ];
  render(
    <DataTable
      label="Typed columns"
      data={rows}
      getRowId={(row) => row.id}
      columns={[
        { accessorKey: "name", header: "Name", kind: "name" },
        { accessorKey: "slug", header: "Slug", kind: "slug" },
        { accessorKey: "enabled", header: "Enabled", kind: "boolean" },
        { accessorKey: "tags", header: "Tags", kind: "tags" },
        { accessorKey: "status", header: "State", kind: "pill" },
        { accessorKey: "created", header: "Created", kind: "datetime" },
      ]}
    />,
  );
  expect(screen.getByText("c/C++")).toBeDefined();
  expect(screen.getByText("c-c-plus-plus").tagName).toBe("CODE");
  expect(screen.queryByText("C-c-plus-plus")).toBeNull();
  expect(screen.getByRole("img", { name: "No" })).toBeDefined();
  expect(screen.getByText("C#")).toBeDefined();
  expect(screen.getByText(".NET")).toBeDefined();
  expect(screen.getByText("Approved").getAttribute("data-variant")).toBe("success");
  expect(document.querySelector("time")?.getAttribute("datetime")).toBe("2026-09-12T12:00:00.000Z");
});

it("keeps explicit custom renderers and supports custom pill colors", () => {
  render(
    <DataTable
      label="Custom columns"
      data={[{ id: "one", value: "high" }]}
      getRowId={(row) => row.id}
      columns={[
        {
          id: "custom",
          accessorKey: "value",
          header: "Custom",
          kind: "slug",
          cell: () => <Link href="/detail">Details</Link>,
        },
        { id: "priority", accessorKey: "value", header: "Priority", kind: "pill", tone: "danger" },
      ]}
    />,
  );
  expect(screen.getByRole("link", { name: "Details" })).toBeDefined();
  expect(screen.getByText("High").getAttribute("data-variant")).toBe("danger");
});

it("preserves false and zero and uses a consistent missing-value marker", () => {
  render(
    <div>
      <div data-testid="empty">
        <ColumnValue value={null} kind="slug" />
      </div>
      <div data-testid="zero">
        <ColumnValue value={0} kind="number" />
      </div>
      <div data-testid="boolean">
        <ColumnValue value="false" kind="boolean" />
      </div>
      <div data-testid="tags">
        <ColumnValue value={[]} kind="tags" />
      </div>
    </div>,
  );
  expect(within(screen.getByTestId("zero")).getByText("0")).toBeDefined();
  for (const key of ["empty", "boolean", "tags"])
    expect(within(screen.getByTestId(key)).getByText("—")).toBeDefined();
});
