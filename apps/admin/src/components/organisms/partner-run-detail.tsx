"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { PageHeading } from "@/components/molecules/page-heading";
import { InfoPanel, DataValue } from "@/components/molecules/info-panel";
import { DateTime } from "@/components/molecules/date-time";
import { StatusBadge } from "@/components/molecules/status-badge";
import { RequestState } from "@/components/molecules/request-state";
import { DataTable } from "@/components/molecules/data-table";
import { useRequest } from "@/lib/use-request";
import { useRefreshInterval } from "@/lib/use-refresh-interval";
import { adminPartnerPipelineGet, adminPartnerEvaluationGet } from "@/lib/api/generated/admin";
import type { PartnerPipelineOut, PartnerEvaluationDetail } from "@/lib/api/generated/models";
import {
  partnerRunLabels,
  partnerRunHref,
  partnerRunPath,
  partnerProductHref,
  partnerOperation,
  type PartnerRunKind,
} from "@/lib/partner-runs";
import { partnerHref, partnershipTrail } from "./partner-connections";
import { RelatedPartnerRuns } from "./partner-runs";
import { JobLogs } from "./job-logs";

export type PartnerRunSection = "details" | "related" | "results" | "logs";
const linked = (href: string, label: string) => (
  <Link href={href} prefetch={false} className="text-blue-700 dark:text-blue-400 hover:underline">
    {label}
  </Link>
);

export function PartnerRunDetail({
  kind,
  id,
  section = "details",
}: {
  kind: PartnerRunKind;
  id: string;
  section?: PartnerRunSection;
}) {
  const interval = useRefreshInterval();
  const [revision, setRevision] = useState(0);
  const load = useCallback(
    async (signal: AbortSignal): Promise<PartnerPipelineOut | PartnerEvaluationDetail> =>
      kind === "pipeline"
        ? adminPartnerPipelineGet(id, { signal })
        : adminPartnerEvaluationGet(id, { signal }),
    [kind, id],
  );
  const result = useRequest(`partner-${kind}/${id}/${revision}`, load, interval * 1000);
  const run = result.data;
  const evaluation = run && "snapshot" in run ? run : undefined;
  const pipeline = run && "operation" in run ? run : undefined;
  const tabs: PartnerRunSection[] = [
    "details",
    "related",
    ...(kind === "evaluations" ? ["results" as const] : []),
    "logs",
  ];
  return (
    <section className="space-y-6">
      <PageHeading
        title={`Run ${id.slice(0, 8)}`}
        browserTitle={`${partnerRunLabels[kind]} · ${id.slice(0, 8)}`}
        trail={[...partnershipTrail, { label: partnerRunLabels[kind], href: partnerRunPath(kind) }]}
      />
      <nav aria-label="Object sections" className="flex gap-5 overflow-x-auto border-b">
        {tabs.map((tab) => (
          <Link
            key={tab}
            prefetch={false}
            href={partnerRunHref(kind, id) + (tab === "details" ? "" : `/${tab}`)}
            aria-current={section === tab ? "page" : undefined}
            className={`whitespace-nowrap border-b-2 px-1 pb-3 text-sm ${section === tab ? "border-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground"}`}
          >
            {tab === "related" ? "Related objects" : tab[0].toUpperCase() + tab.slice(1)}
          </Link>
        ))}
      </nav>
      <RequestState
        loading={result.loading}
        error={result.error}
        retry={() => setRevision((n) => n + 1)}
      />
      {run && section === "details" && (
        <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
          <div className="space-y-6">
            <InfoPanel
              title={pipeline ? "Pipeline job" : "Evaluation"}
              fields={
                pipeline
                  ? [
                      { label: "Operation", value: partnerOperation(pipeline.operation) },
                      {
                        label: "Partner",
                        value: linked(partnerHref(pipeline.provider), pipeline.provider),
                      },
                      {
                        label: "Product",
                        value: pipeline.product_id ? (
                          linked(
                            partnerProductHref(pipeline.product_id),
                            pipeline.product_name ?? pipeline.external_id ?? pipeline.product_id,
                          )
                        ) : (
                          <DataValue value={pipeline.external_id} />
                        ),
                      },
                      {
                        label: "Platform product ID",
                        value: <DataValue value={pipeline.external_id} />,
                      },
                      {
                        label: "Parent run",
                        value: pipeline.parent_id
                          ? linked(
                              partnerRunHref("pipeline", pipeline.parent_id),
                              pipeline.parent_id,
                            )
                          : "—",
                      },
                    ]
                  : evaluation
                    ? [
                        {
                          label: "Product",
                          value: linked(
                            partnerProductHref(evaluation.product_id),
                            evaluation.snapshot.product.name,
                          ),
                        },
                        { label: "Product revision", value: evaluation.snapshot.product.revision },
                        { label: "Articles evaluated", value: evaluation.snapshot.articles.length },
                        {
                          label: "Matches",
                          value: evaluation.result?.decisions.filter((d) => d.relevant).length ?? 0,
                        },
                        {
                          label: "Results",
                          value:
                            evaluation.status === "succeeded"
                              ? evaluation.current
                                ? "Current"
                                : "Outdated — product or article data changed"
                              : "Not completed",
                        },
                      ]
                    : []
              }
            />
            <InfoPanel
              title="Execution"
              fields={[
                { label: "Status", value: <StatusBadge value={run.status} /> },
                { label: "Attempts", value: run.attempts },
                { label: "Available at", value: <DateTime value={run.available_at} /> },
                { label: "Error", value: <DataValue value={run.error} /> },
              ]}
            />
          </div>
          <InfoPanel
            title="Record information"
            fields={[
              { label: "ID", value: <DataValue value={run.id} /> },
              { label: "Created", value: <DateTime value={run.created_at} /> },
              {
                label: "Finished",
                value: run.finished_at ? <DateTime value={run.finished_at} /> : "—",
              },
            ]}
          />
        </div>
      )}
      {run && section === "related" && (
        <div className="space-y-8">
          <InfoPanel
            title="Linked records"
            fields={[
              ...(run.product_id
                ? [
                    {
                      label: "Product",
                      value: linked(
                        partnerProductHref(run.product_id),
                        evaluation?.snapshot.product.name ??
                          pipeline?.product_name ??
                          run.product_id,
                      ),
                    },
                  ]
                : []),
              ...(pipeline
                ? [
                    {
                      label: "Partner",
                      value: linked(partnerHref(pipeline.provider), pipeline.provider),
                    },
                    ...(pipeline.parent_id
                      ? [
                          {
                            label: "Parent run",
                            value: linked(
                              partnerRunHref("pipeline", pipeline.parent_id),
                              pipeline.parent_id,
                            ),
                          },
                        ]
                      : []),
                  ]
                : []),
            ]}
          />
          {pipeline?.operation === "sync" && (
            <RelatedPartnerRuns
              kind="pipeline"
              filters={{ parent_id: id }}
              title="Product sync jobs"
            />
          )}
          {pipeline?.product_id && (
            <RelatedPartnerRuns kind="evaluations" filters={{ product_id: pipeline.product_id }} />
          )}
          {evaluation && (
            <DataTable
              label="Evaluated articles"
              data={evaluation.snapshot.articles}
              getRowId={(article) => article.id}
              columns={[
                {
                  id: "title",
                  header: "Article",
                  accessorKey: "title",
                  cell: ({ row }) =>
                    linked(`/content/articles/${row.original.id}`, row.original.title),
                },
                { id: "type", header: "Type", accessorKey: "content_type" },
                {
                  id: "match",
                  header: "Result",
                  accessorFn: (article) => {
                    const decision = evaluation.result?.decisions.find(
                      (d) => d.article_id === article.id,
                    );
                    return decision
                      ? decision.relevant
                        ? "Relevant"
                        : "No match"
                      : "Not evaluated";
                  },
                },
              ]}
              empty="No articles in this evaluation."
            />
          )}
        </div>
      )}
      {evaluation && section === "results" && <EvaluationResults run={evaluation} />}
      {run && (section === "logs" || section === "details") && (
        <JobLogs kind={kind === "pipeline" ? "partner-pipeline" : "partner-evaluation"} id={id} />
      )}
    </section>
  );
}

function EvaluationResults({ run }: { run: PartnerEvaluationDetail }) {
  return (
    <div className="space-y-6">
      {run.status === "succeeded" && !run.current && (
        <p role="status" className="text-sm text-muted-foreground">
          These results are outdated because product or article data changed.
        </p>
      )}
      {!run.result?.decisions.length && (
        <p className="text-sm text-muted-foreground">
          {run.status === "failed"
            ? "Evaluation failed. See Details and Logs for the error."
            : run.status === "succeeded"
              ? "No article decisions were recorded."
              : "Evaluation results will appear when this run completes."}
        </p>
      )}
      {run.result?.decisions.map((decision) => {
        const article = run.snapshot.articles.find((a) => a.id === decision.article_id);
        const evidence = run.snapshot.product.evidence?.[decision.evidence_index];
        return (
          <InfoPanel
            key={decision.article_id}
            title={article?.title ?? "Article"}
            fields={[
              {
                label: "Article",
                value: linked(
                  `/content/articles/${decision.article_id}`,
                  article?.title ?? decision.article_id,
                ),
              },
              {
                label: "Result",
                value: (
                  <StatusBadge
                    value={decision.relevant ? "Relevant" : "No match"}
                    tone={decision.relevant ? "success" : "neutral"}
                  />
                ),
              },
              { label: "Reason", value: <DataValue value={decision.reason} /> },
              { label: "Technology", value: <DataValue value={decision.technology} /> },
              { label: "Article evidence", value: <DataValue value={decision.article_quote} /> },
              ...(evidence
                ? [
                    {
                      label: "Product evidence",
                      value: (
                        <>
                          <p className="whitespace-pre-wrap">{evidence.quote}</p>
                          <DataValue value={evidence.url} />
                        </>
                      ),
                    },
                  ]
                : []),
            ]}
          />
        );
      })}
    </div>
  );
}
