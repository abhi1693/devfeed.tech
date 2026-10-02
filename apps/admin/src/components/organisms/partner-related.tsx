"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { DataTable } from "@/components/molecules/data-table";
import { useRequest } from "@/lib/use-request";
import { useRefreshInterval } from "@/lib/use-refresh-interval";
import { adminPartnerToolsList } from "@/lib/api/generated/admin";
import { productColumns } from "./partner-tools";
import { RelatedPartnerRuns } from "./partner-runs";

export function PartnerRelated({ provider }: { provider: string }) {
  return (
    <div className="space-y-8">
      <RelatedProducts provider={provider} />
      <RelatedPartnerRuns kind="pipeline" filters={{ provider }} />
      <RelatedPartnerRuns kind="evaluations" filters={{ provider }} />
    </div>
  );
}

function RelatedProducts({ provider }: { provider: string }) {
  const refreshSeconds = useRefreshInterval();
  const [page, setPage] = useState({ offset: 0, limit: 25 });
  const [revision, setRevision] = useState(0);
  const load = useCallback(
    (signal: AbortSignal) => adminPartnerToolsList({ provider, ...page }, { signal }),
    [provider, page],
  );
  const result = useRequest(
    `partner-products/${provider}/${JSON.stringify(page)}/${revision}`,
    load,
    refreshSeconds * 1000,
  );
  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold">Products{result.data ? ` (${result.data.total})` : ""}</h2>
        <Link
          prefetch={false}
          href={`/partnerships/products?provider=${encodeURIComponent(provider)}`}
          className="text-xs text-blue-700 dark:text-blue-400 hover:underline"
        >
          View full list
        </Link>
      </div>
      <DataTable
        label="Partner products"
        data={result.data?.items ?? []}
        columns={productColumns}
        getRowId={(row) => row.id}
        columnChoices
        loading={result.loading}
        error={result.error}
        onRetry={() => setRevision((n) => n + 1)}
        pagination={{
          ...page,
          total: result.data?.total ?? 0,
          onChange: (values) =>
            setPage((current) => ({
              offset: Number(values.offset ?? current.offset),
              limit: Number(values.limit ?? current.limit),
            })),
        }}
        empty="No products synced from this partner yet."
      />
    </section>
  );
}
