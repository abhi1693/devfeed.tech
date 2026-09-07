"use client";

import { useId, useState, type ReactNode } from "react";
import { useTable, tableFeatures, rowPaginationFeature, rowSortingFeature, columnVisibilityFeature, type ColumnDef, type RowData } from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ArrowUpDown, Columns3 } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/atoms/table";
import { Combobox } from "@/components/molecules/combobox";
import { RequestState } from "@/components/molecules/request-state";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/atoms/popover";
import { cn } from "@/lib/utils";

export const dataTableFeatures = tableFeatures({ rowPaginationFeature, rowSortingFeature, columnVisibilityFeature });
type ColumnStyle = { className?: string; headerClassName?: string; sortLabel?: string; label?: string };
export type DataTableColumn<T extends RowData> = ColumnDef<typeof dataTableFeatures, T> & { meta?: ColumnStyle };
type Pagination = { offset: number; limit: number; total: number; onChange: (values: Record<string, string>) => void };
type Props<T extends RowData> = {
  label: string;
  data: T[];
  columns: DataTableColumn<T>[];
  getRowId: (row: T) => string;
  sort?: string;
  onSortChange?: (sort: string) => void;
  pagination?: Pagination;
  loading?: boolean;
  error?: Error;
  onRetry?: () => void;
  empty?: ReactNode;
  className?: string;
  rowClassName?: string;
  toolbar?: ReactNode;
  columnChoices?: boolean;
  initialVisibility?: Record<string, boolean>;
};

/** All admin tables share TanStack's row, column, sorting and pagination models.
 * API lists remain server-paginated; small previews render their supplied rows. */
export function DataTable<T extends RowData>({ label, data, columns, getRowId, sort, onSortChange, pagination, loading = false, error, onRetry, empty = "No records.", className, rowClassName, toolbar, columnChoices = false, initialVisibility = {} }: Props<T>) {
  const pageSizeId = useId();
  const [columnVisibility, setColumnVisibility] = useState(initialVisibility);
  const currentPage = { pageIndex: Math.floor((pagination?.offset ?? 0) / (pagination?.limit ?? 25)), pageSize: pagination?.limit ?? 25 };
  const currentSort = sort ? [{ id: sort.replace(/^-/, ""), desc: sort.startsWith("-") }] : [];
  const table = useTable({
    features: dataTableFeatures, columns, data, getRowId,
    defaultColumn: { enableSorting: !!onSortChange },
    manualPagination: true, manualSorting: true, rowCount: pagination?.total ?? data.length,
    state: { pagination: currentPage, sorting: currentSort, columnVisibility },
    onColumnVisibilityChange: setColumnVisibility,
    onPaginationChange: updater => {
      const next = typeof updater === "function" ? updater(currentPage) : updater;
      pagination?.onChange({ offset: String(next.pageIndex * next.pageSize), limit: String(next.pageSize) });
    },
    onSortingChange: updater => {
      const next = typeof updater === "function" ? updater(currentSort) : updater;
      onSortChange?.(next[0] ? `${next[0].desc ? "-" : ""}${next[0].id}` : "");
    },
    enableSortingRemoval: false,
  });
  return <div className="min-w-0 space-y-3">
    {(toolbar || columnChoices) && <div className="flex flex-wrap items-center gap-3">
      {toolbar}
      {columnChoices && <Popover><PopoverTrigger asChild><Button variant="outline" size="sm" className="ml-auto"><Columns3 aria-hidden />Columns</Button></PopoverTrigger>
        <PopoverContent align="end" aria-label="Table columns" className="w-56 p-2">
          <p className="px-2 py-1.5 text-sm font-medium">Show columns</p>
          <div className="max-h-72 overflow-y-auto">{table.getAllLeafColumns().map(column => <label key={column.id} className="flex cursor-pointer items-center gap-3 rounded px-2 py-2 text-sm hover:bg-muted has-disabled:cursor-default has-disabled:text-muted-foreground">
            <input type="checkbox" className="size-4 accent-primary" checked={column.getIsVisible()} disabled={!column.getCanHide()} onChange={event => column.toggleVisibility(event.target.checked)} />
            {(column.columnDef.meta as ColumnStyle | undefined)?.label ?? (typeof column.columnDef.header === "string" ? column.columnDef.header : column.id)}
          </label>)}</div>
          <div className="mt-1 border-t pt-1"><Button variant="ghost" size="sm" className="w-full justify-start" onClick={() => table.setColumnVisibility(initialVisibility)}>Reset columns</Button></div>
        </PopoverContent>
      </Popover>}
    </div>}
    <div className="overflow-hidden rounded-lg border bg-card">
    <Table aria-label={label} aria-busy={loading} className={className}>
      <TableHeader className="bg-muted/40">{table.getHeaderGroups().map(group => <TableRow key={group.id}>
        {group.headers.map(header => {
          const meta = header.column.columnDef.meta as ColumnStyle | undefined;
          const sorted = header.column.getIsSorted();
          const Icon = sorted === "asc" ? ArrowUp : sorted === "desc" ? ArrowDown : ArrowUpDown;
          return <TableHead scope="col" key={header.id} colSpan={header.colSpan} className={cn("px-3", header.column.id === "actions" && "w-px whitespace-nowrap text-right", meta?.headerClassName)}
            aria-sort={header.column.getCanSort() ? sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : "none" : undefined}>
            {header.isPlaceholder ? null : header.column.getCanSort() ? <Button variant="ghost" size="sm" className="-ml-2" aria-label={meta?.sortLabel} onClick={header.column.getToggleSortingHandler()}>
              <table.FlexRender header={header} /><Icon aria-hidden className="size-3.5 text-muted-foreground" />
            </Button> : <table.FlexRender header={header} />}
          </TableHead>;
        })}
      </TableRow>)}</TableHeader>
      <TableBody>{loading || error ? <TableRow><TableCell colSpan={table.getVisibleLeafColumns().length} className="whitespace-normal p-6">
        <RequestState loading={loading} error={error} retry={onRetry} />
      </TableCell></TableRow> : table.getRowModel().rows.length ? table.getRowModel().rows.map(row => <TableRow key={row.id} className={rowClassName}>
        {row.getVisibleCells().map(cell => <TableCell key={cell.id} className={cn("px-3 py-2", cell.column.id === "actions" && "w-px whitespace-nowrap text-right", (cell.column.columnDef.meta as ColumnStyle | undefined)?.className)}><table.FlexRender cell={cell} /></TableCell>)}
      </TableRow>) : <TableRow><TableCell colSpan={table.getVisibleLeafColumns().length} className="h-36 whitespace-normal px-6 text-center text-sm">{empty}</TableCell></TableRow>}</TableBody>
    </Table>
    {pagination && <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground">
      <span>{loading ? "Loading…" : error ? "Could not load records" : data.length ? `${pagination.offset + 1}–${pagination.offset + data.length} of ${pagination.total}` : pagination.total ? `0 on this page · ${pagination.total} total` : "0 records"}</span>
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={pageSizeId}>Rows</label>
        <Combobox id={pageSizeId} label="Rows per page" className="w-20" required value={String(pagination.limit)} onChange={value => pagination.onChange({ limit: value, offset: "0" })}
          options={[10, 25, 50, 100].map(value => ({ value: String(value), label: String(value) }))} />
        <Button variant="outline" size="sm" disabled={loading || !table.getCanPreviousPage()} onClick={() => table.previousPage()}>Previous</Button>
        <Button variant="outline" size="sm" disabled={loading || error !== undefined || !table.getCanNextPage()} onClick={() => table.nextPage()}>Next</Button>
      </div>
    </div>}
    </div>
  </div>;
}
