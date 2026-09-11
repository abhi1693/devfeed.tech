"use client";
import { LoadingSkeleton } from "./loading-skeleton";

import { useEffect, useId, useState } from "react";
import type { ChimelyClient } from "@chimely/client";
import { preferencesChanged } from "@devfeed/ui/notifications";
import { userRequest } from "@/lib/user";
import { createInboxClient, type InboxConfig } from "@/lib/inbox";
import { AccountGate, useUser } from "./user-account";
import { UserSettingsLayout } from "./user-settings-layout";
import { notificationDefaults, topicNotificationCategory, useNotificationPreferences, type NotificationDisplay } from "./notification-preferences-provider";

type InboxState = { client?: ChimelyClient; enabled: boolean; disabled?: boolean; failed?: boolean };
type Values = { display: NotificationDisplay; enabled: boolean };

export function NotificationSettings() {
  return <AccountGate returnTo="/settings/notifications"><UserSettingsLayout section="notifications"><NotificationContent /></UserSettingsLayout></AccountGate>;
}
function NotificationContent() {
  const { user } = useUser();
  const { value, unavailable, refresh } = useNotificationPreferences();
  const [state, setState] = useState<InboxState>();
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!user) return;
    const controller = new AbortController();
    let client: ChimelyClient | undefined;
    void (async () => {
      const config = await userRequest<InboxConfig>("notifications/config", { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]) });
      if (controller.signal.aborted) return;
      if (!config.enabled || !config.environment || !config.subscriber_id) { setState({ enabled: true, disabled: true }); return; }
      client = createInboxClient(config, user.csrf_token);
      const preferences = await client.getPreferences();
      const enabled = preferences.find(item => item.channel === "in_app" && item.category === topicNotificationCategory)?.enabled ?? true;
      if (!controller.signal.aborted) setState({ client, enabled });
    })().catch(() => { if (!controller.signal.aborted) setState({ enabled: true, failed: true }); });
    return () => { controller.abort(); client?.close(); };
  }, [user, revision]);
  if (unavailable) return <section className="profile-load-error" role="status"><h2>Couldn’t load notification settings</h2><p>Your saved preferences are unchanged.</p><button className="settings-button" onClick={refresh}>Retry</button></section>;
  if (!value || !state) return <LoadingSkeleton kind="form" label="Loading notification settings…" />;
  return <NotificationForm key={user!.user_id} initial={{ display: value, enabled: state.enabled }} inbox={state} onInboxSaved={enabled => setState(previous => previous && ({ ...previous, enabled }))} retry={() => setRevision(value => value + 1)} />;
}
function Toggle({ label, description, checked, disabled, onChange }: { label: string; description: string; checked: boolean; disabled?: boolean; onChange: (checked: boolean) => void }) {
  const id = useId();
  return <div className="settings-field settings-toggle">
    <label htmlFor={id}>{label}</label>
    <input id={id} aria-describedby={`${id}-help`} type="checkbox" checked={checked} disabled={disabled} onChange={event => onChange(event.target.checked)} />
    <p id={`${id}-help`}>{description}</p>
  </div>;
}
function NotificationForm({ initial, inbox, retry, onInboxSaved }: { initial: Values; inbox: InboxState; retry: () => void; onInboxSaved: (enabled: boolean) => void }) {
  const { save } = useNotificationPreferences();
  const snapshot = JSON.stringify(initial);
  const [baseline, setBaseline] = useState(snapshot);
  const [value, setValue] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState(false);
  const dirty = JSON.stringify(value) !== baseline;
  // Match the admin form: refreshed preferences must not erase pending edits.
  if (snapshot !== baseline && !busy) {
    const previous = JSON.parse(baseline) as Values;
    setValue({ display: JSON.stringify(value.display) === JSON.stringify(previous.display) ? initial.display : value.display, enabled: value.enabled === previous.enabled ? initial.enabled : value.enabled });
    setBaseline(snapshot);
  }
  function change(next: Values) { setValue(next); setMessage(""); setError(false); }
  return <section className="profile-panel" aria-label="Notification preferences">
    <form className="profile-form" onSubmit={async event => {
      event.preventDefault(); if (busy || !dirty) return;
      setBusy(true); setMessage(""); setError(false);
      let inboxSaved = false;
      try {
        if (inbox.client) {
          await inbox.client.setPreferences([{ category: topicNotificationCategory, channel: "in_app", enabled: value.enabled }]);
          inboxSaved = true;
          onInboxSaved(value.enabled);
        }
        const display = await save(value.display);
        const saved = { display, enabled: value.enabled };
        setValue(saved); setBaseline(JSON.stringify(saved));
        setMessage("Notification settings saved.");
      } catch {
        setError(true);
        setMessage(inboxSaved ? "Article notification choices saved, but badge and sound settings could not be saved. Retry Save changes." : "Couldn’t save notification settings. Your changes are still here; please try again.");
      } finally {
        if (inboxSaved) window.dispatchEvent(new Event(preferencesChanged));
        setBusy(false);
      }
    }}>
      <fieldset disabled={busy}>
        <legend className="sr-only">Notification preferences</legend>
        <Toggle label="Show unread badge" description="Display the notification count on the bell." checked={value.display.show_badge} onChange={show_badge => change({ ...value, display: { ...value.display, show_badge } })} />
        <Toggle label="Notification sound" description="Play a quiet sound for new notifications while the app is open, after you interact with the page." checked={value.display.sound} onChange={sound => change({ ...value, display: { ...value.display, sound } })} />
        <div className="notification-event-settings">
          <h2>Inbox events</h2>
          <Toggle label="New articles from followed topics" description="Notify me when a new article is published in a topic I follow." checked={value.enabled} disabled={!inbox.client} onChange={enabled => change({ ...value, enabled })} />
          {inbox.client ? <p className="settings-help">Muting hides new and existing article notifications. Turning this back on restores the history. Your followed topics stay unchanged.</p> : <div className="notification-connection" role="status">
            <p>{inbox.disabled ? "Inbox delivery is disabled for this app. Your badge and sound preferences can still be saved." : "Could not load article notification preferences. Your badge and sound preferences can still be saved."}</p>
            <button type="button" className="settings-button settings-button-ghost" onClick={retry}>Retry connection</button>
          </div>}
        </div>
      </fieldset>
      {message && <p className={`profile-feedback${error ? " error" : ""}`} role={error ? "alert" : "status"}>{message}</p>}
      <div className="profile-form-actions">
        <button type="button" className="settings-button settings-button-ghost" disabled={busy} onClick={() => change({ display: notificationDefaults, enabled: inbox.client ? true : value.enabled })}>Reset to defaults</button>
        <button type="submit" className="settings-button" disabled={busy || !dirty} aria-busy={busy}>{busy ? "Saving…" : "Save changes"}</button>
      </div>
      {dirty && <p className="profile-unsaved" role="status">You have unsaved changes.</p>}
    </form>
  </section>;
}
