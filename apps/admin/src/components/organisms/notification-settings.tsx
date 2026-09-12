"use client";
import { useEffect, useState } from "react";
import type { ChimelyClient } from "@chimely/client";
import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import { SettingsForm, SettingToggle } from "@/components/molecules/settings-form";
import { DataTable } from "@/components/molecules/data-table";
import { RequestState } from "@/components/molecules/request-state";
import { useAdmin } from "@/components/molecules/admin-session";
import { adminNotificationConfig } from "@/lib/api/generated/admin";
import { createInboxClient } from "@/lib/inbox";
import { defaultSettings } from "@/lib/settings";
import { useSettings } from "@/lib/use-settings";
import {
  notificationCategories,
  notificationChoices,
  notificationEvents,
  notificationPreferences,
  preferencesChanged,
  inheritNotificationPreferences,
} from "@/lib/notification-preferences";
import { notify } from "@/lib/notifications";

export function NotificationSettingsForm() {
  const { settings, save } = useSettings();
  const admin = useAdmin();
  const [state, setState] = useState<{
    client?: ChimelyClient;
    choices: Record<string, boolean>;
    disabled?: boolean;
    error?: Error;
  }>();
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let disposed = false;
    let client: ChimelyClient | undefined;
    void (async () => {
      const config = await adminNotificationConfig();
      if (!config.enabled) {
        if (!disposed) setState({ choices: {}, disabled: true });
        return;
      }
      client = createInboxClient(config, admin.csrf_token);
      const preferences = await inheritNotificationPreferences(client);
      if (!disposed) setState({ client, choices: notificationChoices(preferences) });
    })().catch(() => {
      if (!disposed)
        setState({ choices: {}, error: new Error("Notification settings are unavailable.") });
    });
    return () => {
      disposed = true;
      client?.close();
    };
  }, [admin.csrf_token, revision]);
  if (!state) return <RequestState loading />;
  const available = !!state.client && !state.error;
  return (
    <SettingsForm
      initial={{ display: settings.notifications, choices: state.choices }}
      defaults={{
        display: defaultSettings.notifications,
        choices: available ? notificationChoices([]) : {},
      }}
      onSave={async (value) => {
        if (state.client) await state.client.setPreferences(notificationPreferences(value.choices));
        try {
          await save("notifications", value.display);
        } catch (error) {
          if (state.client)
            notify.warning(
              "Inbox choices saved; badge and sound settings could not be saved. Retry Save changes.",
            );
          throw error;
        }
        setState((previous) => previous && { ...previous, choices: value.choices });
        window.dispatchEvent(new Event(preferencesChanged));
      }}
    >
      {(value, change) => (
        <>
          <div className="space-y-5">
            <SettingToggle
              label="Show unread badge"
              description="Display the notification count on the bell."
              checked={value.display.show_badge}
              onChange={(show_badge) =>
                change({ ...value, display: { ...value.display, show_badge } })
              }
            />
            <SettingToggle
              label="Notification sound"
              description="Play a quiet sound for new notifications while the app is open, after you interact with the page."
              checked={value.display.sound}
              onChange={(sound) => change({ ...value, display: { ...value.display, sound } })}
            />
          </div>
          {available ? (
            <div className="space-y-3 border-t pt-5">
              <h2 className="text-sm font-semibold">Inbox events</h2>
              <p className="text-sm text-muted-foreground">
                Choose events for each type of background job.
              </p>
              <DataTable
                className="text-xs [&_th]:whitespace-normal [&_th]:px-1 [&_td]:px-1 sm:text-sm sm:[&_th]:px-3 sm:[&_td]:px-3"
                label="Notification preferences"
                data={notificationCategories}
                getRowId={(row) => row.id}
                columns={[
                  {
                    id: "category",
                    header: "Job type",
                    cell: ({ row }) => (
                      <span className="whitespace-normal">{row.original.label}</span>
                    ),
                  },
                  ...notificationEvents.map((event) => ({
                    id: event.id,
                    header: event.label,
                    meta: { className: "text-center", headerClassName: "text-center" },
                    cell: ({
                      row,
                    }: {
                      row: { original: (typeof notificationCategories)[number] };
                    }) => {
                      const key = `${row.original.id}.${event.id}`;
                      return (
                        <Input
                          type="checkbox"
                          aria-label={`${row.original.label}: ${event.label}`}
                          checked={value.choices[key] !== false}
                          onChange={(e) =>
                            change({
                              ...value,
                              choices: { ...value.choices, [key]: e.target.checked },
                            })
                          }
                        />
                      );
                    },
                  })),
                ]}
              />
              <p className="text-xs text-muted-foreground">
                Event choices apply to new notifications. Muting all events in a category also hides
                its earlier notifications.
              </p>
            </div>
          ) : (
            <div role="status" className="space-y-3 rounded-md border bg-muted/30 p-4 text-sm">
              <p>
                {state.disabled
                  ? "Inbox delivery is disabled for this app. Your badge and sound preferences can still be saved."
                  : "Could not load inbox event preferences. Your badge and sound preferences can still be saved."}
              </p>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => setRevision((value) => value + 1)}
              >
                Retry connection
              </Button>
            </div>
          )}
        </>
      )}
    </SettingsForm>
  );
}
