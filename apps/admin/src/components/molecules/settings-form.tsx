"use client";

import { useState, type ReactNode } from "react";
import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import { Field } from "./field";
import { ValidationErrors } from "./validation-errors";
import { notify, notifyFailure } from "@/lib/notifications";

/** One save/reset lifecycle for all personal settings sections. */
export function SettingsForm<T extends object>({
  initial,
  defaults,
  onSave,
  children,
  disabled = false,
}: {
  initial: T;
  defaults: T;
  onSave: (value: T) => Promise<void>;
  disabled?: boolean;
  children: (value: T, change: (value: T) => void) => ReactNode;
}) {
  const snapshot = JSON.stringify(initial);
  const [baseline, setBaseline] = useState(snapshot);
  const [value, setValue] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error>();
  const dirty = JSON.stringify(value) !== baseline;
  // Keep unsaved edits when a background preference check completes.
  if (snapshot !== baseline && !busy) {
    const previous = JSON.parse(baseline) as T;
    const merged = { ...value };
    for (const key of Object.keys(initial) as (keyof T)[]) {
      if (JSON.stringify(value[key]) === JSON.stringify(previous[key])) merged[key] = initial[key];
    }
    setBaseline(snapshot);
    setValue(merged);
  }
  return (
    <form
      className="space-y-6"
      onSubmit={async (event) => {
        event.preventDefault();
        if (busy || disabled || !dirty) return;
        setBusy(true);
        setError(undefined);
        try {
          await onSave(value);
          setBaseline(JSON.stringify(value));
          notify.success("Settings saved");
        } catch (cause) {
          setError(cause instanceof Error ? cause : new Error("Save failed"));
          notifyFailure(cause, "Could not save settings");
        } finally {
          setBusy(false);
        }
      }}
    >
      <fieldset disabled={busy || disabled} className="min-w-0 space-y-6">
        {children(value, setValue)}
      </fieldset>
      <ValidationErrors error={error} />
      {error && (
        <p role="alert" className="text-sm text-destructive">
          Your changes could not all be saved. Check the connection and try again.
        </p>
      )}
      <div className="flex flex-wrap items-center justify-end gap-3 border-t pt-4">
        <Button
          type="button"
          variant="ghost"
          disabled={busy || disabled}
          onClick={() => {
            setValue(defaults);
            setError(undefined);
          }}
        >
          Reset to defaults
        </Button>
        <Button type="submit" disabled={!dirty || disabled} loading={busy} loadingText="Saving…">
          Save changes
        </Button>
      </div>
      {dirty && (
        <p className="text-xs text-muted-foreground" role="status">
          You have unsaved changes.
        </p>
      )}
    </form>
  );
}

export function SettingToggle({
  label,
  description,
  checked,
  onChange,
}: {
  label: string;
  description?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <Field orientation="horizontal" label={label} subtext={description}>
      {(control) => (
        <Input
          {...control}
          type="checkbox"
          className="sm:mt-2.5"
          checked={checked}
          onChange={(event) => onChange(event.target.checked)}
        />
      )}
    </Field>
  );
}
