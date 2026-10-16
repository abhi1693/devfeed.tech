"use client";

import { useCallback } from "react";
import {
  AudienceTargeting,
  hasAudience,
  type Audience,
  type AudienceTopicLoader,
} from "@devfeed/ui/audience-targeting";
import {
  adminPartnerAudienceTopics,
  adminPartnerAudienceTopicsCatalog,
} from "@/lib/api/generated/admin";

export function PartnerAudience({
  accountId,
  value,
  onChange,
  enabled,
  disabled,
}: {
  accountId?: string;
  value: Audience;
  onChange?: (value: Audience) => void;
  enabled: boolean;
  disabled?: boolean;
}) {
  const loadTopics = useCallback<AudienceTopicLoader>(
    (dimension, params, signal) =>
      accountId
        ? adminPartnerAudienceTopics(accountId, { dimension, ...params }, { signal })
        : adminPartnerAudienceTopicsCatalog({ dimension, ...params }, { signal }),
    [accountId],
  );
  return (
    <section aria-label="Audience targeting" className="space-y-4 border-t pt-6">
      <div className="space-y-1">
        <h2 className="font-semibold">Audience targeting</h2>
        <p className="text-sm text-muted-foreground">
          Any match within a group qualifies. Selected groups must all match the reader’s profile.
        </p>
        {!enabled && (
          <p className="text-sm text-muted-foreground">
            Advanced targeting requires Platinum or Diamond.
            {hasAudience(value) &&
              " Saved restrictions are retained. These placements stay disabled until the account has an eligible tier or the restrictions are explicitly cleared."}
          </p>
        )}
      </div>
      <AudienceTargeting
        key={accountId ?? "new-account"}
        value={value}
        onChange={enabled ? onChange : undefined}
        disabled={disabled}
        loadTopics={loadTopics}
      />
      {!enabled && onChange && hasAudience(value) && (
        <button
          type="button"
          className="rounded-md border px-3 py-2 text-sm hover:bg-muted"
          disabled={disabled}
          onClick={() =>
            onChange({ experience_levels: [], language_topic_ids: [], tool_topic_ids: [] })
          }
        >
          Clear saved audience restrictions
        </button>
      )}
    </section>
  );
}
