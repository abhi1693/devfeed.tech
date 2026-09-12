"use client";
import { recordHref } from "@/lib/routes";
import Link from "next/link";
import { useCallback } from "react";
import { getRecord } from "@/lib/resource-api";
import { type Resource, resources } from "@/lib/resources";
import { useRequest } from "@/lib/use-request";
export function RecordLink({
  resource,
  id,
  label,
}: {
  resource: Resource;
  id: string;
  label?: string;
}) {
  const load = useCallback(
    (signal: AbortSignal) => (label ? Promise.resolve(null) : getRecord(resource, id, signal)),
    [resource, id, label],
  );
  const { data } = useRequest(`${resource}/${id}/${label}`, load);
  const text = label || (data && String(data[resources[resource].title])) || id.slice(0, 8);
  return (
    <Link
      prefetch={false}
      href={recordHref(resource, { id })}
      className="text-blue-700 dark:text-blue-400 hover:underline break-words"
      title={id}
    >
      {text}
    </Link>
  );
}
