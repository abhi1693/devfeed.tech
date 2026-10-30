"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { Button } from "@devfeed/ui/button";
import { Textarea } from "@devfeed/ui/textarea";
import { ResourcePanel } from "@devfeed/ui/resource-layout";
import { StatusBadge } from "@/components/molecules/status-badge";
import { RequestState } from "@/components/molecules/request-state";
import { DateTime } from "@/components/molecules/date-time";
import { useAdmin } from "@/components/molecules/admin-session";
import { reviewArticleMaintenance, pipelineJobHref, pipelineLabels } from "@/lib/article-pipeline";
import type { MaintenanceDetail } from "@/lib/api/generated/models";
import { ApiError } from "@/lib/api/client";
import { humanize } from "@/lib/resources";
import { notify, notifyFailure } from "@/lib/notifications";

export function ArticleMaintenance({
  value,
  onUpdated,
}: {
  value: MaintenanceDetail;
  onUpdated: () => void;
}) {
  const admin = useAdmin();
  const pending = useRef(false);
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<Error>();
  async function review(action: "approve" | "reject") {
    if (pending.current || !value.allowed_actions.includes(action)) return;
    const trimmed = note.trim();
    if (action === "reject" && !trimmed) return;
    pending.current = true;
    setBusy(action);
    setError(undefined);
    try {
      await reviewArticleMaintenance(
        value.article_id,
        value.id,
        { action, expected_version: value.version, ...(trimmed ? { note: trimmed } : {}) },
        admin.csrf_token,
      );
      notify.success(
        action === "approve" ? "Published replacement approved" : "Private refresh discarded",
      );
      onUpdated();
    } catch (cause) {
      setError(
        cause instanceof ApiError && cause.status === 409
          ? new Error("This refresh changed. Reloading its current version before review.")
          : cause instanceof Error
            ? cause
            : new Error("Could not review this refresh. Try again."),
      );
      notifyFailure(cause, "Could not review the private refresh");
      onUpdated();
    } finally {
      pending.current = false;
      setBusy(null);
    }
  }
  const replacement = value.replacement;
  return (
    <ResourcePanel title="Private refresh">
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm font-medium">{pipelineLabels[value.target]}</p>
          <StatusBadge
            value={value.status}
            label={value.ready ? "Ready for review" : humanize(value.status)}
          />
        </div>
        <p className="text-sm text-muted-foreground">
          Readers keep seeing the approved article while this replacement is prepared. Discarding
          this refresh keeps the published article available.
        </p>
        <p className="text-xs text-muted-foreground">
          Requested <DateTime value={value.created_at} />.
        </p>
        {!!value.reasons.length && (
          <ul className="list-disc space-y-1 pl-5 text-sm" aria-label="Refresh requirements">
            {value.reasons.map((reason) => (
              <li key={reason}>{humanize(reason)}</li>
            ))}
          </ul>
        )}
        <section className="space-y-3 rounded-md border p-4" aria-label="Replacement preview">
          <h3 className="font-semibold">{replacement.title}</h3>
          <p className="whitespace-pre-wrap text-sm">{replacement.summary}</p>
          {replacement.description && (
            <p className="text-sm text-muted-foreground">{replacement.description}</p>
          )}
          <dl className="grid gap-2 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-muted-foreground">Language</dt>
              <dd>{replacement.language || "Not set"}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Type and format</dt>
              <dd>
                {humanize(replacement.content_type)} · {humanize(replacement.content_format)}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Topics</dt>
              <dd>
                {replacement.topics
                  .map((topic) => `${topic.name}${topic.role === "primary" ? " (primary)" : ""}`)
                  .join(", ") || "None"}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Tags</dt>
              <dd>{replacement.tags.map((tag) => tag.name).join(", ") || "None"}</dd>
            </div>
          </dl>
          {replacement.image_url && (
            <a
              href={replacement.image_url}
              target="_blank"
              rel="noreferrer"
              className="break-all text-sm text-primary hover:underline"
            >
              View replacement image (opens in a new tab)
            </a>
          )}
        </section>
        <ul className="space-y-2" aria-label="Private processing runs">
          {(["enrichment", "analysis", "images"] as const).map((key) => {
            const job = value.jobs[key];
            return job ? (
              <li
                key={key}
                className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-3 text-sm"
              >
                <Link
                  prefetch={false}
                  href={pipelineJobHref(key, job.id)}
                  className="text-primary hover:underline"
                >
                  {pipelineLabels[key]} run
                </Link>
                <StatusBadge value={job.status} />
              </li>
            ) : null;
          })}
        </ul>
        <RequestState error={error} />
        {!!value.allowed_actions.length && (
          <div className="space-y-3">
            <label className="block space-y-2 text-sm">
              <span>
                Review note <span className="text-muted-foreground">(required to discard)</span>
              </span>
              <Textarea
                value={note}
                maxLength={1000}
                disabled={busy !== null}
                onChange={(event) => setNote(event.target.value)}
              />
            </label>
            <div className="flex flex-wrap gap-3">
              {value.allowed_actions.includes("approve") && (
                <Button
                  disabled={busy !== null || !value.ready}
                  loading={busy === "approve"}
                  loadingText="Approving…"
                  onClick={() => void review("approve")}
                >
                  Approve replacement
                </Button>
              )}
              {value.allowed_actions.includes("reject") && (
                <Button
                  variant="outline"
                  disabled={busy !== null || !note.trim()}
                  loading={busy === "reject"}
                  loadingText="Discarding…"
                  onClick={() => void review("reject")}
                >
                  Discard refresh
                </Button>
              )}
            </div>
          </div>
        )}
      </div>
    </ResourcePanel>
  );
}
