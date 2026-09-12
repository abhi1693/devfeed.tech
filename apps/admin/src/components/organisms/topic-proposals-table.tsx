"use client";

import { DateTime } from "@/components/molecules/date-time";

import Link from "next/link";
import { useMemo, type ReactNode } from "react";
import { ExternalLink } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { DataTable, type DataTableColumn } from "@/components/molecules/data-table";
import type { BulkAction } from "@/components/molecules/table-bulk-actions";
import { analysisActive } from "@/components/molecules/topic-analysis-control";
import { Check, X, Sparkles, Trash2 } from "lucide-react";
import {
  adminTopicProposalReview,
  adminTopicProposalAnalyze,
  adminTopicProposalDelete,
} from "@/lib/api/generated/admin";
import { Badge } from "@/components/atoms/badge";
import { useAdmin } from "@/components/molecules/admin-session";
import { actorLabel } from "@/lib/actor-label";
import { humanize } from "@/lib/resources";
import { StatusBadge } from "@/components/molecules/status-badge";
import { TopicProposalActions } from "@/components/molecules/topic-proposal-actions";
import type { PageTopicProposalOut, TopicProposalOut } from "@/lib/api/generated/models";

const emptyRows: TopicProposalOut[] = [];
type Props = {
  page?: PageTopicProposalOut;
  loading: boolean;
  error?: Error;
  status: string;
  filtered: boolean;
  sort: string;
  limit: number;
  offset: number;
  onChange: (values: Record<string, string>) => void;
  onRetry: () => void;
  onClearFilters: () => void;
  toolbar?: ReactNode;
  selectionKey?: string;
  loadAllRows?: (signal: AbortSignal) => Promise<TopicProposalOut[]>;
};

function Terms({ values, label }: { values?: string[]; label: string }) {
  if (!values?.length) return <span className="text-muted-foreground">—</span>;
  return (
    <div
      className="flex min-w-0 max-w-48 items-center gap-1"
      title={values.join(", ")}
      aria-label={`${values.length} ${label}: ${values.join(", ")}`}
    >
      {values.slice(0, 2).map((value) => (
        <Badge
          key={value}
          variant="secondary"
          title={value}
          className="min-w-0 max-w-24 shrink rounded-md"
        >
          <span className="min-w-0 truncate">{value}</span>
        </Badge>
      ))}
      {values.length > 2 && (
        <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
          +{values.length - 2}
        </span>
      )}
    </div>
  );
}

function ProposalEvidence({ proposal }: { proposal: TopicProposalOut }) {
  const github = proposal.evidence.find(
    (item) =>
      item.provider === "github/explore" &&
      typeof item.source_url === "string" &&
      item.source_url.startsWith("https://github.com/github/explore/blob/"),
  );
  if (github)
    return (
      <a
        className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground hover:underline"
        href={String(github.source_url)}
        target="_blank"
        rel="noopener noreferrer"
      >
        View source
        <ExternalLink aria-hidden className="size-3" />
      </a>
    );
  const articleIds = new Set<string>();
  for (const item of proposal.evidence) {
    if (typeof item.article_id === "string") articleIds.add(item.article_id);
    if (Array.isArray(item.articles))
      for (const article of item.articles) {
        if (
          article &&
          typeof article === "object" &&
          "id" in article &&
          typeof article.id === "string"
        )
          articleIds.add(article.id);
      }
  }
  if (articleIds.size)
    return (
      <Link
        className="text-xs text-muted-foreground hover:text-foreground hover:underline"
        href={
          articleIds.size === 1
            ? `/content/articles/${encodeURIComponent([...articleIds][0])}`
            : `/taxonomy/topics/proposals/${proposal.id}#proposal-evidence`
        }
      >
        {articleIds.size} {articleIds.size === 1 ? "article" : "articles"}
      </Link>
    );
  const imported = proposal.evidence.find((item) => typeof item.row === "number");
  return (
    <span className="text-xs text-muted-foreground">
      {imported
        ? `Import row ${String(imported.row)}`
        : proposal.evidence.length
          ? `${proposal.evidence.length} evidence records`
          : "No evidence"}
    </span>
  );
}

export function TopicProposalsTable({
  page,
  loading,
  error,
  status,
  filtered,
  sort,
  limit,
  offset,
  onChange,
  onRetry,
  onClearFilters,
  toolbar,
  selectionKey,
  loadAllRows,
}: Props) {
  const admin = useAdmin();
  const options = { headers: { "X-CSRF-Token": admin.csrf_token } };
  const pending = (proposal: TopicProposalOut) =>
    proposal.status === "pending" && !analysisActive(proposal.analysis);
  const bulkActions: BulkAction<TopicProposalOut>[] = [
    {
      id: "approve",
      label: "Approve",
      icon: <Check aria-hidden />,
      description:
        "Apply the selected proposals to the active topic catalog. Changed proposals will require a fresh review.",
      eligible: (proposal) => pending(proposal) && !!proposal.content_hash,
      run: (proposal) =>
        adminTopicProposalReview(
          proposal.id,
          {
            decision: "approved",
            expected_input_hash: proposal.content_hash,
            topic: proposal.proposed,
          },
          options,
        ),
    },
    {
      id: "reject",
      label: "Reject",
      icon: <X aria-hidden />,
      description: "Reject the selected proposals. They will remain available in the Rejected tab.",
      eligible: (proposal) => pending(proposal) && !!proposal.content_hash,
      run: (proposal) =>
        adminTopicProposalReview(
          proposal.id,
          { decision: "rejected", expected_input_hash: proposal.content_hash },
          options,
        ),
    },
    {
      id: "analyze",
      label: "AI analysis",
      icon: <Sparkles aria-hidden />,
      description:
        "Queue research for the selected proposals to fill missing information. Results still need your approval before becoming active topics.",
      eligible: (proposal) =>
        pending(proposal) &&
        ["description", "aliases", "keywords", "website_url", "logo_url", "facts"].some((field) => {
          const value = proposal.proposed[field as keyof typeof proposal.proposed];
          return !value || (Array.isArray(value) && !value.length);
        }),
      run: (proposal) => adminTopicProposalAnalyze(proposal.id, options),
    },
    {
      id: "delete",
      label: "Delete",
      icon: <Trash2 aria-hidden />,
      destructive: true,
      description:
        "Permanently delete the selected proposals and their completed AI research. Active topics will remain. Imported topics may be proposed again on a later import. This cannot be undone.",
      eligible: (proposal) => !analysisActive(proposal.analysis) && !!proposal.content_hash,
      run: (proposal) =>
        adminTopicProposalDelete(
          proposal.id,
          { expected_input_hash: proposal.content_hash!, expected_status: proposal.status },
          options,
        ),
    },
  ];
  const columns = useMemo<DataTableColumn<TopicProposalOut>[]>(
    () => [
      {
        id: "slug",
        accessorFn: (proposal) => proposal.proposed.slug,
        header: "Topic",
        enableSorting: true,
        sortDescFirst: false,
        enableHiding: false,
        meta: { className: "min-w-56", sortLabel: "Sort by topic slug" },
        cell: ({ row: { original: proposal } }) => (
          <>
            <Link
              prefetch={false}
              href={`/taxonomy/topics/proposals/${proposal.id}`}
              className="block max-w-72 truncate font-medium hover:underline"
              title={proposal.proposed.name}
            >
              {proposal.proposed.name}
            </Link>
            <span
              className="mt-0.5 block max-w-72 truncate text-xs text-muted-foreground"
              title={proposal.proposed.description || proposal.proposed.slug}
            >
              {proposal.proposed.description || proposal.proposed.slug}
            </span>
          </>
        ),
      },
      {
        id: "kind",
        accessorFn: (proposal) => proposal.proposed.kind,
        header: "Kind",
        enableSorting: false,
        cell: ({ row }) => (
          <Badge variant="secondary">{humanize(row.original.proposed.kind)}</Badge>
        ),
      },
      {
        id: "description",
        accessorFn: (proposal) => proposal.proposed.description,
        header: "Description",
        enableSorting: false,
        meta: { className: "min-w-64 max-w-80 whitespace-normal text-xs text-muted-foreground" },
        cell: ({ row }) => row.original.proposed.description || "—",
      },
      {
        id: "canonical_slug",
        accessorFn: (proposal) => proposal.proposed.slug,
        header: "Slug",
        enableSorting: false,
      },
      {
        accessorKey: "action",
        header: "Change",
        enableSorting: false,
        cell: ({ row }) => (row.original.action === "create" ? "New topic" : "Update topic"),
      },
      {
        id: "keywords",
        header: "Keywords",
        enableSorting: false,
        cell: ({ row }) => <Terms values={row.original.proposed.keywords} label="keywords" />,
      },
      {
        id: "aliases",
        header: "Aliases",
        enableSorting: false,
        cell: ({ row }) => <Terms values={row.original.proposed.aliases} label="aliases" />,
      },
      {
        accessorKey: "source_name",
        header: "Source",
        enableSorting: false,
        cell: ({ row }) => (
          <div className="space-y-1">
            <span className="block max-w-44 truncate text-xs" title={row.original.source_name}>
              {row.original.source_name}
            </span>
            <ProposalEvidence proposal={row.original} />
          </div>
        ),
      },
      {
        id: "evidence",
        header: "Evidence",
        enableSorting: false,
        cell: ({ row }) => <ProposalEvidence proposal={row.original} />,
      },
      {
        accessorKey: "status",
        header: "Status",
        enableSorting: false,
        cell: ({ row }) => <StatusBadge value={row.original.status} />,
      },
      {
        id: "analysis",
        header: "AI analysis",
        enableSorting: false,
        cell: ({ row }) =>
          row.original.analysis ? (
            <Link
              className="text-xs hover:underline"
              href={`/taxonomy/topics/proposals/${row.original.id}#proposal-evidence`}
            >
              {row.original.analysis.status === "succeeded"
                ? row.original.analysis.outcome === "enriched"
                  ? "Ready for review"
                  : "No additions"
                : humanize(row.original.analysis.status)}
            </Link>
          ) : (
            <span className="text-xs text-muted-foreground">Not run</span>
          ),
      },
      {
        accessorKey: "created_at",
        header: "Submitted",
        enableSorting: true,
        meta: { className: "text-xs text-muted-foreground", sortLabel: "Sort by submitted date" },
        cell: ({ row }) => <DateTime value={row.original.created_at} dateOnly />,
      },
      {
        id: "submitted_by",
        header: "Submitted by",
        enableSorting: false,
        cell: ({ row }) => (
          <span
            className="block max-w-48 truncate text-xs"
            title={actorLabel(row.original.created_by, admin)}
          >
            {actorLabel(row.original.created_by, admin)}
          </span>
        ),
      },
      {
        id: "reviewed_by",
        header: "Reviewed by",
        enableSorting: false,
        cell: ({ row }) => (
          <span
            className="block max-w-48 truncate text-xs"
            title={actorLabel(row.original.reviewed_by, admin)}
          >
            {actorLabel(row.original.reviewed_by, admin)}
          </span>
        ),
      },
      {
        id: "reviewed_at",
        accessorKey: "reviewed_at",
        header: "Reviewed",
        enableSorting: false,
        cell: ({ row }) =>
          row.original.reviewed_at ? <DateTime value={row.original.reviewed_at} /> : "—",
      },
      {
        id: "actions",
        header: "Actions",
        enableSorting: false,
        enableHiding: false,
        meta: { className: "min-w-44" },
        cell: ({ row }) => <TopicProposalActions proposal={row.original} onReviewed={onRetry} />,
      },
    ],
    [onRetry, admin],
  );

  return (
    <DataTable
      label="Topic proposals"
      columns={columns}
      data={page?.items ?? emptyRows}
      getRowId={(row) => row.id}
      getRowLabel={(row) => row.proposed.name}
      selectionKey={selectionKey ?? status}
      bulkActions={bulkActions}
      onBulkComplete={onRetry}
      loadAllRows={loadAllRows}
      loading={loading}
      error={error}
      onRetry={onRetry}
      className="min-w-[1080px]"
      toolbar={toolbar}
      columnChoices
      initialVisibility={{
        description: false,
        canonical_slug: false,
        aliases: false,
        evidence: false,
        submitted_by: false,
        reviewed_by: false,
        reviewed_at: false,
      }}
      sort={sort}
      onSortChange={(value) => onChange({ sort: value, offset: "0" })}
      pagination={{ offset, limit, total: page?.total ?? 0, onChange }}
      empty={
        <>
          <p className="font-medium">
            {page?.total
              ? "No proposals on this page"
              : filtered
                ? "No proposals match these filters"
                : `No ${status} proposals`}
          </p>
          <p className="mt-1 text-muted-foreground">
            {page?.total
              ? "Return to the first page to see the remaining proposals."
              : filtered
                ? "Try another search or clear your filters."
                : status === "pending"
                  ? "Discover or import topics to add suggestions for review."
                  : `Topics you ${status === "approved" ? "approve" : "reject"} will appear here.`}
          </p>
          {page?.total ? (
            <Button
              variant="link"
              size="sm"
              className="mt-2"
              onClick={() => onChange({ offset: "0" })}
            >
              First page
            </Button>
          ) : (
            filtered && (
              <Button variant="link" size="sm" className="mt-2" onClick={onClearFilters}>
                Clear filters
              </Button>
            )
          )}
        </>
      }
    />
  );
}
