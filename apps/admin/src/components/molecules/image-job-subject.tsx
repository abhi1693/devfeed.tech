"use client";

import Link from "next/link";
import { RecordLink } from "./record-link";
import type { RecordData } from "@/lib/resource-api";
import { humanize } from "@/lib/resources";
import { partnerProductHref } from "@/lib/partner-runs";

export function imageJobOperation(record: RecordData) {
  const details = record.details as Record<string, unknown> | null | undefined;
  const operation = String(details?.operation ?? "");
  if (operation === "product-image") return "Product featured image";
  if (operation === "product-logo") return "Product logo";
  return operation ? humanize(operation.replaceAll("-", " ")) : "—";
}

export function ImageJobSubject({ record }: { record: RecordData }) {
  const label = record.target_name ? String(record.target_name) : undefined;
  if (record.product_id)
    return (
      <Link
        href={partnerProductHref(String(record.product_id))}
        prefetch={false}
        className="break-words text-primary hover:underline"
      >
        {label ?? `Product ${String(record.product_id).slice(0, 8)}`}
      </Link>
    );
  if (record.article_id)
    return <RecordLink resource="articles" id={String(record.article_id)} label={label} />;
  if (record.source_id)
    return <RecordLink resource="sources" id={String(record.source_id)} label={label} />;
  if (record.topic_id)
    return <RecordLink resource="topics" id={String(record.topic_id)} label={label} />;
  return <span className="text-muted-foreground">{label ?? "—"}</span>;
}
