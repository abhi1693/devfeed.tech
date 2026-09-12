"use client";

import { useMemo } from "react";
import { adminTopicProposalGet, adminTopicReplacementsList } from "@/lib/api/generated/admin";
import { getRecord } from "@/lib/resource-api";
import { EntityPicker, type EntityPickerSource } from "./entity-picker";
import type { ComboboxProps } from "./combobox";

export function TopicReplacementPicker({
  topicId,
  ...props
}: Omit<ComboboxProps, "label" | "options"> & { topicId: string }) {
  const source = useMemo<EntityPickerSource>(
    () => ({
      key: `topic-replacements/${topicId}`,
      list: async (params, signal) => {
        const page = await adminTopicReplacementsList(
          { q: params.q, offset: params.offset, limit: params.limit, exclude_topic_id: topicId },
          { signal },
        );
        return { ...page, items: page.items.map((item) => ({ ...item })) };
      },
      get: async (id, signal) => {
        if (!id.startsWith("proposal:")) return getRecord("topics", id, signal);
        const proposal = await adminTopicProposalGet(id.slice("proposal:".length), { signal });
        return { ...proposal.proposed, id, status: proposal.status };
      },
    }),
    [topicId],
  );
  return <EntityPicker {...props} resource="topics" label="Replacement topic" source={source} />;
}
