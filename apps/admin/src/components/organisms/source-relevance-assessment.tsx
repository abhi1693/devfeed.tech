"use client";

import { useState } from "react";
import { Button } from "@/components/atoms/button";
import { InfoPanel } from "@/components/molecules/info-panel";
import { DateTime } from "@/components/molecules/date-time";
import { StatusBadge } from "@/components/molecules/status-badge";

function object(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
function text(value: unknown) {
  return typeof value === "string" ? value : "";
}
const labels = {
  relevant: "In scope",
  unrelated: "Outside scope",
  uncertain: "Uncertain",
  missing: "Not assessed",
} as const;
type Classification = keyof typeof labels;
function classification(value: unknown): Classification {
  return value === "relevant" || value === "unrelated" || value === "uncertain" ? value : "missing";
}
function percent(value: number) {
  return `${Number((value * 100).toFixed(2))}%`;
}

export function SourceRelevanceAssessment({
  assessment,
  approvalStatus,
}: {
  assessment: unknown;
  approvalStatus: unknown;
}) {
  const [filter, setFilter] = useState<Classification | "all">("all");
  const [page, setPage] = useState(0);
  const data = object(assessment);
  const sample = Array.isArray(data.sample) ? data.sample.map(object) : [];
  const entries = Array.isArray(data.entries) ? data.entries.map(object) : [];
  const rows = sample.map((item) => {
    const matches = entries.filter(
      (entry) => typeof item.index === "number" && entry.index === item.index,
    );
    const entry = matches.length === 1 ? matches[0] : {};
    return {
      title: text(item.title),
      summary: text(item.summary),
      classification: classification(entry.relevance),
      evidence: text(entry.evidence),
    };
  });
  const filtered = rows.filter((row) => filter === "all" || row.classification === filter);
  const pageCount = Math.max(1, Math.ceil(filtered.length / 5));
  const currentPage = Math.min(page, pageCount - 1);
  const visibleRows = filtered.slice(currentPage * 5, (currentPage + 1) * 5);
  const counts = { relevant: 0, unrelated: 0, uncertain: 0, missing: 0 };
  rows.forEach((row) => counts[row.classification]++);
  const confidence =
    typeof data.confidence === "number" &&
    Number.isFinite(data.confidence) &&
    data.confidence >= 0 &&
    data.confidence <= 1
      ? data.confidence
      : null;
  const hasAssessment = Object.keys(data).length > 0;
  const supported =
    data.approval_supported === true
      ? "approval"
      : data.rejection_supported === true
        ? "rejection"
        : null;
  // Explain known policy versions only. Stored backend flags remain authoritative.
  const knownPolicy = [
    "source-relevance-v1",
    "source-relevance-v2",
    "source-relevance-v3",
    "source-relevance-v4",
    "source-relevance-v5",
  ].includes(text(data.version));
  const sourceLevelApproval = data.version === "source-relevance-v5";
  const required = Math.ceil(sample.length * 0.8);
  const blockers: string[] = [];
  if (knownPolicy && !supported) {
    if (sample.length < 3)
      blockers.push(`Only ${sample.length} usable entries; at least 3 are required.`);
    if (confidence !== null && confidence < 0.9)
      blockers.push(`Confidence is ${percent(confidence)}; at least 90% is required.`);
    if (sample.length >= 3) {
      if (data.relevance === "relevant" && counts.relevant < (sourceLevelApproval ? 1 : required))
        blockers.push(
          sourceLevelApproval
            ? "The assessment needs at least one in-scope entry with validated evidence."
            : `${counts.relevant} of ${sample.length} entries are in scope; approval needs at least ${required} of ${sample.length} (80%).`,
        );
      else if (data.relevance === "unrelated" && counts.unrelated < required)
        blockers.push(
          `${counts.unrelated} of ${sample.length} entries are outside scope; rejection needs at least ${required} of ${sample.length} (80%).`,
        );
      else if (data.relevance !== "relevant" && data.relevance !== "unrelated")
        blockers.push("The overall assessment does not clearly support approval or rejection.");
    }
  }
  const headline = !hasAssessment
    ? "No assessment available"
    : supported
      ? `Evidence supports automatic ${supported}`
      : "No automatic decision";
  return (
    <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.4fr)]">
      <InfoPanel title="Source relevance assessment">
        <div className="space-y-6 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-base font-semibold">{headline}</h3>
            <span className="flex items-center gap-2 text-xs text-muted-foreground">
              Source status <StatusBadge value={approvalStatus} />
            </span>
          </div>
          {!hasAssessment ? (
            <p className="text-muted-foreground">
              No relevance assessment has been saved. Check Related objects for review jobs and
              errors.
            </p>
          ) : (
            <>
              {["source-relevance-v1", "source-relevance-v2", "source-relevance-v3"].includes(
                text(data.version),
              ) && (
                <p className="rounded-lg border p-3 text-muted-foreground">
                  This assessment used the earlier developer-only scope. Re-analysis is needed to
                  evaluate product management, engineering leadership, and related careers under the
                  broader source policy.
                </p>
              )}
              {knownPolicy && !sourceLevelApproval && (
                <p className="rounded-lg border p-3 text-muted-foreground">
                  This saved assessment used the previous 80% approval rule. Re-analysis under the
                  current policy can approve a high-confidence in-scope source even with mixed
                  recent articles. Articles are reviewed separately.
                </p>
              )}
              {!supported && (
                <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950/30">
                  <p className="font-medium">
                    {approvalStatus === "pending"
                      ? "Why this source is still pending"
                      : "Why this assessment could not decide"}
                  </p>
                  <ul className="mt-2 list-disc space-y-1 pl-5">
                    {(blockers.length
                      ? blockers
                      : [
                          "The saved assessment does not confirm enough validated evidence for an automatic decision.",
                        ]
                    ).map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                  <p className="mt-3 text-xs text-muted-foreground">
                    Uncertain entries are not evidence for rejection. They still count in the sample
                    total.
                  </p>
                </div>
              )}
              <dl className="grid gap-4 rounded-lg bg-muted/40 p-4 sm:grid-cols-3">
                <div>
                  <dt className="text-xs text-muted-foreground">Overall assessment</dt>
                  <dd className="mt-1 font-medium">{labels[classification(data.relevance)]}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">Model confidence</dt>
                  <dd className="mt-1 font-medium">
                    {confidence === null ? "Not reported" : percent(confidence)}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">Sample size</dt>
                  <dd className="mt-1 font-medium">{sample.length} entries</dd>
                </div>
              </dl>
              {text(data.reason) && (
                <div>
                  <h4 className="font-medium">Assessment explanation</h4>
                  <p className="mt-1 break-words text-muted-foreground">{text(data.reason)}</p>
                </div>
              )}
              <details className="border-t pt-4">
                <summary className="cursor-pointer text-xs font-medium text-muted-foreground">
                  Assessment details
                </summary>
                {knownPolicy && (
                  <p className="text-xs leading-relaxed text-muted-foreground">
                    {sourceLevelApproval
                      ? "Approval requires an in-scope source assessment with at least 90% confidence, 3 usable entries, and at least one validated in-scope quote. There is no article percentage requirement for approval. Individual articles are reviewed separately. Rejection still requires evidence for at least 80% of sampled entries."
                      : "This assessment required at least 3 usable entries, 90% model confidence, and valid quoted evidence for at least 80% of entries supporting the overall classification."}
                  </p>
                )}
                <dl className="mt-3 grid gap-3 text-xs sm:grid-cols-2">
                  <div>
                    <dt className="text-muted-foreground">Last assessed</dt>
                    <dd className="mt-1">
                      {text(data.checked_at) ? (
                        <DateTime value={text(data.checked_at)} />
                      ) : (
                        "Not recorded"
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Model</dt>
                    <dd className="mt-1 break-words">{text(data.model) || "Not recorded"}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Assessment version</dt>
                    <dd className="mt-1">{text(data.version) || "Not recorded"}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Content window begins</dt>
                    <dd className="mt-1">
                      {text(data.content_not_before) ? (
                        <DateTime value={text(data.content_not_before)} />
                      ) : (
                        "No date restriction"
                      )}
                    </dd>
                  </div>
                </dl>
              </details>
            </>
          )}
        </div>
      </InfoPanel>
      {!!rows.length && (
        <InfoPanel title="Article evidence">
          <p className="mt-1 text-xs text-muted-foreground">
            {counts.relevant} relevant · {counts.unrelated} outside scope · {counts.uncertain}{" "}
            uncertain
            {counts.missing > 0 ? ` · ${counts.missing} not assessed` : ""}
          </p>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
            <label className="flex items-center gap-2 text-sm">
              Show
              <select
                aria-label="Filter article evidence"
                className="rounded-md border bg-background px-2 py-1.5 text-sm"
                value={filter}
                onChange={(event) => {
                  setFilter(event.target.value as Classification | "all");
                  setPage(0);
                }}
              >
                <option value="all">All entries ({rows.length})</option>
                {(Object.keys(labels) as Classification[]).map((value) => (
                  <option key={value} value={value}>
                    {labels[value]} ({counts[value]})
                  </option>
                ))}
              </select>
            </label>
            <span className="text-xs text-muted-foreground" role="status">
              {filtered.length
                ? `${currentPage * 5 + 1}–${Math.min((currentPage + 1) * 5, filtered.length)} of ${filtered.length}`
                : "No matching entries"}
            </span>
          </div>
          <ol aria-label="Sampled articles" className="mt-3 divide-y rounded-lg border px-4">
            {visibleRows.map((row, index) => (
              <li key={`${filter}/${currentPage}/${index}`} className="py-3">
                <details name="source-article-evidence">
                  <summary className="cursor-pointer rounded-sm text-sm focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring">
                    <span className="ml-1 font-medium break-words">
                      {text(row.title) || `Entry ${index + 1}`}
                    </span>
                    <span className="ml-2 inline-block align-middle">
                      <StatusBadge
                        value={row.classification}
                        label={labels[row.classification]}
                        tone={
                          row.classification === "relevant"
                            ? "success"
                            : row.classification === "unrelated"
                              ? "danger"
                              : "warning"
                        }
                      />
                    </span>
                  </summary>
                  <div className="mt-3 space-y-3 pl-4">
                    {text(row.summary) && (
                      <div>
                        <p className="text-xs font-medium text-muted-foreground">Feed summary</p>
                        <p className="mt-1 whitespace-pre-wrap break-words">{text(row.summary)}</p>
                      </div>
                    )}
                    {row.evidence ? (
                      <div>
                        <p className="text-xs font-medium text-muted-foreground">Quoted evidence</p>
                        <blockquote className="mt-1 border-l-2 pl-3 whitespace-pre-wrap break-words">
                          {row.evidence}
                        </blockquote>
                      </div>
                    ) : (
                      <p className="text-xs text-muted-foreground">
                        No supporting quote was recorded for this entry.
                      </p>
                    )}
                  </div>
                </details>
              </li>
            ))}
          </ol>
          {pageCount > 1 && (
            <nav
              aria-label="Article evidence pages"
              className="mt-4 flex items-center justify-between gap-2"
            >
              <Button
                variant="outline"
                size="sm"
                disabled={currentPage === 0}
                onClick={() => setPage(currentPage - 1)}
              >
                Previous
              </Button>
              <span className="text-xs text-muted-foreground">
                Page {currentPage + 1} of {pageCount}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={currentPage + 1 >= pageCount}
                onClick={() => setPage(currentPage + 1)}
              >
                Next
              </Button>
            </nav>
          )}
        </InfoPanel>
      )}
    </div>
  );
}
