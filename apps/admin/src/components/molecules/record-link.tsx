"use client";
import { recordHref } from "@/lib/routes";
import Link from "next/link";
import { useCallback, useMemo } from "react";
import { getRecord } from "@/lib/resource-api";
import { recordIdentity, recordIdentityKey, type SimpleResource } from "@/lib/record-identities";
import { resources } from "@/lib/resources";
import { useRequest } from "@/lib/use-request";
export function RecordLink({
  resource,
  id,
  label,
}: {
  resource: SimpleResource;
  id: string;
  label?: string;
}) {
  const identity = useMemo(() => recordIdentity(resource, { id }), [resource, id]);
  const load = useCallback(
    (signal: AbortSignal) => (label ? Promise.resolve(null) : getRecord(identity, signal)),
    [identity, label],
  );
  const { data } = useRequest(`${recordIdentityKey(identity)}/${label}`, load);
  const text = label || (data && String(data[resources[resource].title])) || id.slice(0, 8);
  return (
    <Link
      prefetch={false}
      href={recordHref(identity)}
      className="text-blue-700 dark:text-blue-400 hover:underline break-words"
      title={id}
    >
      {text}
    </Link>
  );
}
