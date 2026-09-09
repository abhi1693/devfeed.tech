"use client";

import { DataTable, type DataTableColumn } from "./data-table";

export type DeletionImpact = { label: string; count: number; action: string };
const columns: DataTableColumn<DeletionImpact>[] = [
  { id: "label", header: "Records", cell: ({ row }) => <span className="whitespace-normal">{row.original.label}</span> },
  { id: "count", header: "Count", meta: { className: "text-right tabular-nums", headerClassName: "text-right" }, cell: ({ row }) => row.original.count.toLocaleString() },
  { id: "action", header: "Action", cell: ({ row }) => <span className="whitespace-normal">{row.original.action}</span> },
];

export function DeletionImpactTable({ impact }: { impact: DeletionImpact[] }) {
  const affected = impact.filter(row => row.count > 0);
  if (!affected.length) return null;
  return <DataTable label="Deletion impact" data={affected} columns={columns} getRowId={row => row.label} empty="No linked records." />;
}
