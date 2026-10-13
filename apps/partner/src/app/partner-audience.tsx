"use client";

import { useCallback, useState, type FormEvent } from "react";
import {
  AudienceTargeting,
  audienceValue,
  hasAudience,
  type AudienceTopicLoader,
} from "@devfeed/ui/audience-targeting";
import { Button } from "@devfeed/ui/button";
import type { Account } from "@/lib/types";
import { parseAudienceTopics } from "@/lib/contracts";
import { usePartnerPreferenceSave } from "@/lib/use-partner-preference-save";

type Props = {
  account: Account;
  csrfToken: string;
  onSaved: (account: Account) => void;
  onUnavailable: () => void;
};

export function PartnerAudience(props: Props) {
  return <AccountAudience key={props.account.id} {...props} />;
}

function AccountAudience({ account, csrfToken, onSaved, onUnavailable }: Props) {
  const [audience, setAudience] = useState(() => audienceValue(account.audience));
  const {
    request,
    save: savePreferences,
    saving,
    message,
    error,
    clearMessage,
  } = usePartnerPreferenceSave({
    accountId: account.id,
    csrfToken,
    onSaved,
    onUnavailable,
    successMessage: "Audience saved.",
    errorMessage: "Could not save the audience. Check the selections and retry.",
    confirmationMessage: "Could not confirm the saved audience. Refresh and try again.",
  });
  const loadTopics = useCallback<AudienceTopicLoader>(
    async (dimension, params, signal) => {
      const query = new URLSearchParams({ dimension });
      for (const [key, value] of Object.entries(params))
        if (value !== undefined) query.set(key, String(value));
      return request(`audience-topics?${query}`, {
        signal,
        parse: parseAudienceTopics,
        errorMessage: "Could not load audience topics.",
      });
    },
    [request],
  );

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (account.advanced_targeting) await savePreferences({ audience });
  }
  return (
    <section aria-label="Audience targeting" className="mt-6 space-y-4 border-t pt-6">
      <div className="space-y-1">
        <h2>Audience targeting</h2>
        <p className="muted text-sm">
          Any match within a group qualifies. Selected groups must all match the reader’s profile.
        </p>
        {!account.advanced_targeting && (
          <p className="muted text-sm">
            Advanced targeting requires Platinum or Diamond.
            {hasAudience(audience) &&
              " Saved restrictions are retained. These placements stay disabled until the account has an eligible tier or an administrator explicitly clears the restrictions."}
          </p>
        )}
      </div>
      <form aria-label="Audience settings" aria-busy={saving} onSubmit={save} className="space-y-4">
        <AudienceTargeting
          value={audience}
          loadTopics={loadTopics}
          disabled={saving}
          onChange={
            account.advanced_targeting
              ? (value) => {
                  setAudience(value);
                  clearMessage();
                }
              : undefined
          }
        />
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        {message && (
          <p role="status" className="text-sm">
            {message}
          </p>
        )}
        {account.advanced_targeting && (
          <Button type="submit" loading={saving}>
            Save audience
          </Button>
        )}
      </form>
    </section>
  );
}
