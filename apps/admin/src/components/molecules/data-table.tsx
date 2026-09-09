"use client";

import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { useTable, tableFeatures, rowPaginationFeature, rowSortingFeature, columnVisibilityFeature, rowSelectionFeature, type RowSelectionState, type ColumnDef, type RowData } from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ArrowUpDown, Columns3 } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/atoms/table";
import { Select } from "@/components/molecules/select";
import { RequestState } from "@/components/molecules/request-state";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/atoms/popover";
import { cn } from "@/lib/utils";
import { TableBulkActions, type BulkAction, type BulkActionSource } from "./table-bulk-actions";
import { notifyFailure } from "@/lib/notifications";

export const dataTableFeatures = tableFeatures({ rowPaginationFeature, rowSortingFeature, columnVisibilityFeature, rowSelectionFeature });
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
  selectionKey?: string;
  getRowLabel?: (row: T) => string;
  bulkActions?: BulkAction<T>[];
  bulkSources?: BulkActionSource<T>[];
  onBulkComplete?: () => void;
  loadAllRows?: (signal: AbortSignal) => Promise<T[]>;
};

/** All admin tables share TanStack's row, column, sorting and pagination models.
 * API lists remain server-paginated; small previews render their supplied rows. */
export function DataTable<T extends RowData>({ label, data, columns, getRowId, sort, onSortChange, pagination, loading = false, error, onRetry, empty = "No records.", className, rowClassName, toolbar, columnChoices = false, initialVisibility = {}, selectionKey = "", getRowLabel = getRowId, bulkActions = [], bulkSources = [], onBulkComplete, loadAllRows }: Props<T>) {
  const pageSizeId = useId();
  const [columnVisibility, setColumnVisibility] = useState(initialVisibility);
  const [bulkBusy, setBulkBusy] = useState(false);
  const scope = JSON.stringify([label, selectionKey, pagination?.offset, pagination?.limit, sort]);
  const [selection, setSelection] = useState<{ scope: string; rows: RowSelectionState; allRows?: T[] }>({ scope, rows: {} });
  const [selectingAll, setSelectingAll] = useState(false);
  const selectionRequest = useRef<AbortController | null>(null);
  useEffect(() => () => { selectionRequest.current?.abort(); }, [scope]);
  if (selection.scope !== scope) { setSelection({ scope, rows: {} }); setBulkBusy(false); setSelectingAll(false); }
  const rowSelection = selection.scope === scope ? selection.rows : {};
  const disabled = loading || !!error || bulkBusy || selectingAll;
  const selectionColumn = useMemo<DataTableColumn<T>>(() => ({
    id: "selection", enableSorting: false, enableHiding: false,
    meta: { className: "w-10", headerClassName: "w-10", label: "Selection" },
    header: ({ table }) => <Input type="checkbox" aria-label="Select all on this page" className="block"
      checked={table.getIsAllPageRowsSelected()} ref={node => { if (node) node.indeterminate = table.getIsSomePageRowsSelected() && !table.getIsAllPageRowsSelected(); }}
      disabled={disabled || !data.length} onChange={table.getToggleAllPageRowsSelectedHandler()} />,
    cell: ({ row }) => <Input type="checkbox" aria-label={`Select ${getRowLabel(row.original)}`} className="block"
      checked={row.getIsSelected()} disabled={disabled} onChange={row.getToggleSelectedHandler()} />,
  }), [disabled, data.length, getRowLabel]);
  const selectable = bulkActions.length > 0;
  const tableColumns = useMemo(() => selectable ? [selectionColumn, ...columns] : columns, [selectionColumn, columns, selectable]);
  const currentPage = { pageIndex: Math.floor((pagination?.offset ?? 0) / (pagination?.limit ?? 25)), pageSize: pagination?.limit ?? 25 };
  const currentSort = sort ? [{ id: sort.replace(/^-/, ""), desc: sort.startsWith("-") }] : [];
  const table = useTable({
    features: dataTableFeatures, columns: tableColumns, data, getRowId,
    defaultColumn: { enableSorting: !!onSortChange },
    manualPagination: true, manualSorting: true, rowCount: pagination?.total ?? data.length,
    state: { pagination: currentPage, sorting: currentSort, columnVisibility, rowSelection },
    enableRowSelection: true,
    onRowSelectionChange: updater => setSelection(previous => {
      const rows = typeof updater === "function" ? updater(previous.scope === scope ? previous.rows : {}) : updater;
      return { scope, rows, allRows: Object.values(rows).some(Boolean) ? previous.allRows : undefined };
    }),
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
  const selected = selection.allRows
    ? [...new Map([...selection.allRows, ...data].map(row => [getRowId(row), row])).values()].filter(row => rowSelection[getRowId(row)])
    : table.getSelectedRowModel().rows.map(row => row.original);
  async function selectAllMatching() {
    if (!loadAllRows || selectingAll || bulkBusy) return;
    selectionRequest.current?.abort();
    const controller = new AbortController(); selectionRequest.current = controller;
    setSelectingAll(true);
    try {
      const rows = await loadAllRows(controller.signal);
      if (!controller.signal.aborted) setSelection({ scope, allRows: rows, rows: Object.fromEntries(rows.map(row => [getRowId(row), true])) });
    } catch (error) { if (!controller.signal.aborted) notifyFailure(error, "Could not select all matching records"); }
    finally { if (!controller.signal.aborted) setSelectingAll(false); }
  }
  return <div className="min-w-0 space-y-3">
    {(toolbar || columnChoices) && <div className="flex flex-wrap items-center gap-3">
      {toolbar}
      {columnChoices && <Popover><PopoverTrigger asChild><Button variant="outline" size="sm" className="ml-auto"><Columns3 aria-hidden />Columns</Button></PopoverTrigger>
        <PopoverContent align="end" aria-label="Table columns" className="w-56 p-2">
          <p className="px-2 py-1.5 text-sm font-medium">Show columns</p>
          <div className="max-h-72 overflow-y-auto">{table.getAllLeafColumns().filter(column => column.id !== "selection").map(column => <label key={column.id} className="flex cursor-pointer items-center gap-3 rounded px-2 py-2 text-sm hover:bg-muted has-disabled:cursor-default has-disabled:text-muted-foreground">
            <Input type="checkbox" checked={column.getIsVisible()} disabled={!column.getCanHide()} onChange={event => column.toggleVisibility(event.target.checked)} />
            {(column.columnDef.meta as ColumnStyle | undefined)?.label ?? (typeof column.columnDef.header === "string" ? column.columnDef.header : column.id)}
          </label>)}</div>
          <div className="mt-1 border-t pt-1"><Button variant="ghost" size="sm" className="w-full justify-start" onClick={() => table.setColumnVisibility(initialVisibility)}>Reset columns</Button></div>
        </PopoverContent>
      </Popover>}
    </div>}
    {(selectable || bulkSources.length > 0) && <TableBulkActions key={scope} label={label} selected={selected} actions={bulkActions} sources={bulkSources}
      selectionDescription={selection.allRows ? "across all pages" : "on this page"}
      selectAllControl={loadAllRows && !selection.allRows && (pagination?.total ?? 0) > data.length && table.getIsAllPageRowsSelected() && <Button size="sm" variant="link" disabled={loading || !!error || bulkBusy} loading={selectingAll} loadingText="Loading records…" onClick={() => void selectAllMatching()}>Select all {pagination?.total.toLocaleString()} matching records</Button>}
      getRowId={getRowId} getRowLabel={getRowLabel} disabled={loading || !!error || selectingAll} onBusyChange={setBulkBusy}
      onClear={() => { selectionRequest.current?.abort(); setSelectingAll(false); setSelection({ scope, rows: {} }); }} onComplete={ids => {
        table.setRowSelection(previous => Object.fromEntries(Object.entries(previous).filter(([id]) => !ids.includes(id))));
        if (ids.length) onBulkComplete?.();
      }} />}
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
      <TableBody inert={bulkBusy || undefined}>{loading || error ? <TableRow><TableCell colSpan={table.getVisibleLeafColumns().length} className="whitespace-normal p-6">
        <RequestState loading={loading} error={error} retry={onRetry} />
      </TableCell></TableRow> : table.getRowModel().rows.length ? table.getRowModel().rows.map(row => <TableRow key={row.id} className={rowClassName} data-state={row.getIsSelected() ? "selected" : undefined}>
        {row.getVisibleCells().map(cell => <TableCell key={cell.id} className={cn("px-3 py-2", cell.column.id === "actions" && "w-px whitespace-nowrap text-right", (cell.column.columnDef.meta as ColumnStyle | undefined)?.className)}><table.FlexRender cell={cell} /></TableCell>)}
      </TableRow>) : <TableRow><TableCell colSpan={table.getVisibleLeafColumns().length} className="h-36 whitespace-normal px-6 text-center text-sm">{empty}</TableCell></TableRow>}</TableBody>
    </Table>
    {pagination && <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-xs text-muted-foreground">
      <span>{loading ? "Loading…" : error ? "Could not load records" : data.length ? `${pagination.offset + 1}–${pagination.offset + data.length} of ${pagination.total}` : pagination.total ? `0 on this page · ${pagination.total} total` : "0 records"}</span>
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={pageSizeId}>Rows</label>
        <Select id={pageSizeId} label="Rows per page" className="w-20" required value={String(pagination.limit)} onChange={value => pagination.onChange({ limit: value, offset: "0" })}
          options={[10, 25, 50, 100].map(value => ({ value: String(value), label: String(value) }))} />
        <Button variant="outline" size="sm" disabled={loading || !table.getCanPreviousPage()} onClick={() => table.previousPage()}>Previous</Button>
        <Button variant="outline" size="sm" disabled={loading || error !== undefined || !table.getCanNextPage()} onClick={() => table.nextPage()}>Next</Button>
      </div>
    </div>}
    </div>
  </div>;
}
