"use client";
import Link from "next/link";
import { useMemo } from "react";
import { useTable, tableFeatures, rowPaginationFeature, rowSortingFeature, columnVisibilityFeature, type ColumnDef } from "@tanstack/react-table";
import { ArrowDown, ArrowUp } from "lucide-react";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/atoms/table";
import { Button } from "@/components/atoms/button";
import { Combobox } from "@/components/molecules/combobox";
import { StatusBadge } from "@/components/molecules/status-badge";
import { RecordActions } from "@/components/molecules/record-actions";
import { RecordLink } from "@/components/molecules/record-link";
import { type Resource, resources, humanize } from "@/lib/resources";
import type { RecordData, RecordPage } from "@/lib/resource-api";
import { languageName } from "@/lib/languages";
const features = tableFeatures({ rowPaginationFeature, rowSortingFeature, columnVisibilityFeature });
export function RecordTable({ resource, page, sort, onChange }: { resource: Resource; page: RecordPage; sort: string; onChange: (changes: Record<string, string>) => void }) {
  const spec = resources[resource];
  const columns = useMemo<ColumnDef<typeof features, RecordData>[]>(() => [
    ...spec.columns.map(column => ({ id: column.key, accessorKey: column.key, header: column.label, enableSorting: !!column.sort,
      cell: ({ row }: { row: { original: RecordData } }) => {
        const value = row.original[column.key];
        if (value == null || value === "") return <span className="text-muted-foreground">—</span>;
        if (column.resource) return <RecordLink resource={column.resource} id={String(value)} />;
        if (column.key === spec.title || column.key === "id") return <Link prefetch={false} href={`/${resource}/${encodeURIComponent(row.original.id)}`} className="block max-w-lg break-words font-medium text-blue-700 hover:underline">{column.key === "id" ? String(value).slice(0, 8) : String(value)}</Link>;
        if (column.date) return <span className="whitespace-nowrap text-xs">{new Date(String(value)).toLocaleString()}</span>;
        if (column.key === "language") return languageName(String(value));
        if (typeof value === "boolean" || column.key.endsWith("status")) return <StatusBadge value={value} />;
        return humanize(String(value));
      } })),
    ...(!spec.readonly ? [{ id: "actions", header: "Actions", enableSorting: false, cell: ({ row }: { row: { original: RecordData } }) => <RecordActions resource={resource} id={row.original.id} /> }] : []),
  ], [spec, resource]);
  const table = useTable({ features, columns, data: page.items, getRowId: row => row.id, manualPagination: true, manualSorting: true, rowCount: page.total,
    state: { pagination: { pageIndex: Math.floor(page.offset / page.limit), pageSize: page.limit }, sorting: [{ id: sort.replace(/^-/, ""), desc: sort.startsWith("-") }] },
    onPaginationChange: updater => { const current = { pageIndex: Math.floor(page.offset / page.limit), pageSize: page.limit }; const next = typeof updater === "function" ? updater(current) : updater; onChange({ offset: String(next.pageIndex * next.pageSize), limit: String(next.pageSize) }); },
    onSortingChange: updater => { const current = [{ id: sort.replace(/^-/, ""), desc: sort.startsWith("-") }]; const next = typeof updater === "function" ? updater(current) : updater; onChange({ sort: next[0] ? `${next[0].desc ? "-" : ""}${next[0].id}` : spec.defaultSort, offset: "0" }); },
  });
  return <div className="overflow-hidden rounded-lg border bg-card">
    <Table><TableHeader>{table.getHeaderGroups().map(group => <TableRow key={group.id}>{group.headers.map(header => <TableHead key={header.id} className={header.id === "actions" ? "w-24" : ""} aria-sort={header.column.getIsSorted() === "asc" ? "ascending" : header.column.getIsSorted() === "desc" ? "descending" : undefined}>
      {header.column.getCanSort() ? <Button variant="ghost" size="sm" className="-ml-1 h-auto justify-start gap-1 px-1 py-2 text-left has-[>svg]:px-1" onClick={header.column.getToggleSortingHandler()}><table.FlexRender header={header} />{header.column.getIsSorted() === "asc" ? <ArrowUp className="size-3" /> : header.column.getIsSorted() === "desc" ? <ArrowDown className="size-3" /> : null}</Button> : <table.FlexRender header={header} />}
    </TableHead>)}</TableRow>)}</TableHeader><TableBody>{table.getRowModel().rows.length ? table.getRowModel().rows.map(row => <TableRow key={row.id}>{row.getVisibleCells().map(cell => <TableCell key={cell.id}><table.FlexRender cell={cell} /></TableCell>)}</TableRow>) : <TableRow><TableCell colSpan={columns.length} className="h-28 text-center text-muted-foreground">No {spec.label.toLowerCase()} match these filters.</TableCell></TableRow>}</TableBody></Table>
    <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground"><span>{page.items.length ? `${page.offset + 1}–${page.offset + page.items.length} of ${page.total}` : page.total ? `0 on this page · ${page.total} total` : "0 records"}</span><div className="flex flex-wrap items-center gap-2"><label htmlFor={`size-${resource}`}>Rows</label><Combobox id={`size-${resource}`} label="Rows per page" className="w-20" value={String(page.limit)} onChange={value => onChange({ limit: value, offset: "0" })} required options={[10, 25, 50, 100].map(size => ({ value: String(size), label: String(size) }))} /><Button variant="outline" size="sm" disabled={!table.getCanPreviousPage()} onClick={() => table.previousPage()}>Previous</Button><Button variant="outline" size="sm" disabled={!table.getCanNextPage()} onClick={() => table.nextPage()}>Next</Button></div></div>
  </div>;
}
