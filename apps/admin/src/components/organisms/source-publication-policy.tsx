"use client";

import { useState } from "react";
import { Button } from "@/components/atoms/button";
import { InfoPanel } from "@/components/molecules/info-panel";
import { useAdmin } from "@/components/molecules/admin-session";
import { adminSourcePublicationPolicy } from "@/lib/api/generated/admin";
import type { PublicationPolicyUpdate } from "@/lib/api/generated/models";
import { notify, notifyFailure } from "@/lib/notifications";

export function SourcePublicationPolicy({
  id,
  mode,
  revision,
  approved,
  fullAutomation = false,
}: {
  id: string;
  mode: PublicationPolicyUpdate["mode"];
  revision: number;
  approved: boolean;
  fullAutomation?: boolean;
}) {
  const admin = useAdmin();
  const [current, setCurrent] = useState({ mode, revision });
  const [selected, setSelected] = useState(mode);
  const [busy, setBusy] = useState(false);
  async function save() {
    if (busy) return;
    setBusy(true);
    try {
      const result = await adminSourcePublicationPolicy(
        id,
        { mode: selected, expected_revision: current.revision },
        { headers: { "X-CSRF-Token": admin.csrf_token } },
      );
      setCurrent({
        mode: result.publication_policy ?? "manual",
        revision: result.publication_policy_revision ?? 0,
      });
      notify.success("Source publication policy saved");
    } catch (error) {
      notifyFailure(error, "Could not save publication policy");
    } finally {
      setBusy(false);
    }
  }
  if (fullAutomation)
    return (
      <InfoPanel title="Automatic publication">
        <p className="text-sm text-muted-foreground">
          Full automation is enabled. Articles from approved, enabled sources are analyzed and
          published when they pass the evidence checks. Unresolved articles are rejected
          automatically after processing finishes.
        </p>
        <p className="mt-3 text-xs text-muted-foreground">
          The saved source policy applies when full automation is turned off.
        </p>
      </InfoPanel>
    );
  return (
    <InfoPanel title="Automatic publication">
      <p className="mb-4 text-sm text-muted-foreground">
        Preview records what would be published after successful analysis. Automatic publication
        applies the same checks to future results. Uncertain results and articles with human
        editorial changes remain for review.
      </p>
      <label htmlFor={`publication-policy-${id}`} className="mb-2 block text-sm font-medium">
        Publication mode
      </label>
      <div className="flex flex-wrap gap-2">
        <select
          id={`publication-policy-${id}`}
          value={selected}
          disabled={busy || !approved}
          onChange={(event) => setSelected(event.target.value as PublicationPolicyUpdate["mode"])}
          className="h-9 rounded-md border bg-background px-3 text-sm"
        >
          <option value="manual">Manual review</option>
          <option value="preview">Preview automatic decisions</option>
          <option value="auto" disabled={current.mode === "manual"}>
            Publish eligible articles automatically
          </option>
        </select>
        <Button
          disabled={busy || !approved || selected === current.mode}
          loading={busy}
          onClick={() => void save()}
        >
          Save publication mode
        </Button>
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        {!approved
          ? "Approve this source to configure automation."
          : current.mode === "manual"
            ? "Start with preview before enabling automatic publication."
            : `Policy revision ${current.revision}. Existing articles can be evaluated from the overview.`}
      </p>
    </InfoPanel>
  );
}
