"use client";

import { useEffect, useRef, useState, type ComponentProps } from "react";
import type { ChimelyClient, WellKnownPayload } from "@chimely/client";
import {
  ChimelyProvider,
  InboxContent,
  useChimelyClient,
  useNotifications,
  useUnseenCount,
  type InboxAppearance,
} from "@chimely/react";
import { Popover } from "radix-ui";
import { AlertTriangle, Bell, CheckCircle2, Info, XCircle } from "lucide-react";
import { useNotificationSound } from "./use-notification-sound";
import { runWhenPageActive } from "./page-activity";

export const preferencesChanged = "devfeed:notification-preferences";
const appearance: InboxAppearance = {
  variables: {
    colorPrimary: "var(--foreground)",
    colorPrimaryHover: "var(--muted)",
    colorBackground: "var(--card)",
    colorForeground: "var(--card-foreground)",
    colorMuted: "var(--border)",
    colorBadge: "var(--primary)",
    colorBadgeForeground: "var(--primary-foreground)",
    fontFamily: "inherit",
    fontSize: "14px",
    borderRadius: "6px",
  },
  classNames: {
    content: "devfeed-inbox",
    item: "devfeed-notification",
    footer: "devfeed-inbox-footer",
  },
};
type ContentProps = ComponentProps<typeof InboxContent<WellKnownPayload>>;
type InboxProps = {
  client: ChimelyClient;
  prepare?: (client: ChimelyClient, signal: AbortSignal) => Promise<unknown>;
  showBadge?: boolean;
  animateBadge?: boolean;
  sound?: boolean;
  emptyBody: string;
  categoryLabels: Record<string, string>;
  tabs: ContentProps["tabs"];
  actionUrl: (value: unknown) => string | null;
  onNavigate: (href: string) => void;
  footer: { href: string; label: string };
};

export function NotificationBell({
  count = 0,
  animateBadge = false,
  latest = 0,
  loading = false,
  ...props
}: ComponentProps<"button"> & {
  count?: number;
  animateBadge?: boolean;
  latest?: number;
  loading?: boolean;
}) {
  const badge = useRef<HTMLSpanElement>(null);
  const [openedAt] = useState(Date.now);
  const baseline = useRef<{ count: number; latest: number; enabled: boolean } | null>(null);
  useEffect(() => {
    if (loading) return;
    const previous = baseline.current;
    baseline.current = { count, latest, enabled: animateBadge };
    if (
      !animateBadge ||
      !previous?.enabled ||
      count <= 0 ||
      latest <= openedAt ||
      latest <= previous.latest ||
      !badge.current?.animate ||
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    )
      return;
    const animation = badge.current.animate(
      [{ transform: "scale(1)" }, { transform: "scale(1.15)" }, { transform: "scale(1)" }],
      { duration: 180, easing: "ease-out" },
    );
    return () => animation.cancel();
  }, [count, latest, loading, animateBadge, openedAt]);
  return (
    <button
      type="button"
      className="devfeed-notification-bell"
      aria-label={count ? `Notifications (${count} new)` : "Notifications"}
      {...props}
    >
      <Bell size={16} aria-hidden />
      {count > 0 && (
        <span ref={badge} aria-hidden className="devfeed-notification-count">
          {count > 99 ? "99+" : count}
        </span>
      )}
    </button>
  );
}
function Panel({ children }: { children: React.ReactNode }) {
  return (
    <Popover.Portal>
      <Popover.Content
        align="end"
        sideOffset={6}
        collisionPadding={12}
        className="devfeed-inbox-panel"
        aria-label="Notifications"
      >
        {children}
      </Popover.Content>
    </Popover.Portal>
  );
}
export function NotificationsUnavailable({ onRetry }: { onRetry: () => void }) {
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <NotificationBell />
      </Popover.Trigger>
      <Panel>
        <div className="devfeed-inbox-unavailable">
          <h2>Notifications</h2>
          <p role="status">Notifications are temporarily unavailable.</p>
          <button className="devfeed-inbox-button" onClick={onRetry}>
            Retry
          </button>
        </div>
      </Panel>
    </Popover.Root>
  );
}

/** Shared admin inbox; each app supplies its authenticated client and safe routes. */
export function NotificationInbox(props: InboxProps) {
  const { client, prepare } = props;
  const [preferenceError, setPreferenceError] = useState(false);
  useEffect(() => {
    let ready = false;
    return runWhenPageActive((signal) => {
      let preparing = false;
      const connect = async () => {
        if (preparing || signal.aborted) return;
        preparing = true;
        try {
          if (!ready) await prepare?.(client, signal);
          if (!signal.aborted) {
            setPreferenceError(false);
            ready = true;
            client.connect();
          }
        } catch {
          if (!signal.aborted) setPreferenceError(true);
        } finally {
          preparing = false;
        }
      };
      const refresh = () => {
        if (signal.aborted) return;
        if (ready) void client.refresh();
        else void connect();
      };
      void connect();
      const timer = setInterval(refresh, 60_000);
      window.addEventListener(preferencesChanged, refresh);
      return () => {
        clearInterval(timer);
        window.removeEventListener(preferencesChanged, refresh);
        client.close();
      };
    });
  }, [client, prepare]);
  return (
    <ChimelyProvider client={client}>
      <InboxPopover {...props} preferenceError={preferenceError} />
    </ChimelyProvider>
  );
}
function InboxPopover({
  showBadge = true,
  animateBadge = false,
  sound = false,
  emptyBody,
  categoryLabels,
  tabs,
  actionUrl,
  onNavigate,
  footer,
  preferenceError,
}: InboxProps & { preferenceError: boolean }) {
  const client = useChimelyClient();
  const { count } = useUnseenCount();
  const { error, isLoading, items } = useNotifications();
  useNotificationSound(
    count,
    isLoading,
    sound,
    Math.max(0, ...items.map((item) => Date.parse(item.occurredAt))),
  );
  const [open, setOpen] = useState(false);
  return (
    <Popover.Root
      open={open}
      onOpenChange={(value) => {
        setOpen(value);
        if (value && !preferenceError) {
          void client.markAllSeen();
          void client.refresh();
        }
      }}
    >
      <Popover.Trigger asChild>
        <NotificationBell
          count={showBadge ? count : 0}
          animateBadge={animateBadge && showBadge}
          loading={isLoading}
          latest={Math.max(0, ...items.map((item) => Date.parse(item.occurredAt)))}
        />
      </Popover.Trigger>
      <Panel>
        {(error || preferenceError) && (
          <div role="status" className="devfeed-inbox-error">
            <AlertTriangle size={16} aria-hidden />
            <span>Notifications unavailable. Retrying automatically.</span>
            <button
              className="devfeed-inbox-button"
              disabled={isLoading}
              onClick={() => {
                if (preferenceError) window.dispatchEvent(new Event(preferencesChanged));
                else void client.refresh();
              }}
            >
              Retry
            </button>
          </div>
        )}
        <InboxContent
          appearance={appearance}
          localization={{ emptyTitle: "No notifications", emptyBody, categoryLabels }}
          tabs={tabs}
          onItemClick={(item) => {
            const href = actionUrl(item.payload.action_url);
            void client.markRead(item);
            if (href) {
              setOpen(false);
              onNavigate(href);
            }
            return false;
          }}
          renderAvatar={({ item }) => {
            const severity = item.payload.severity;
            const Icon =
              severity === "error"
                ? XCircle
                : severity === "warning"
                  ? AlertTriangle
                  : severity === "success"
                    ? CheckCircle2
                    : Info;
            return (
              <Icon
                aria-label={String(severity ?? "info")}
                className="devfeed-notification-icon"
                data-severity={severity}
              />
            );
          }}
          renderFooter={() => (
            <a
              className="devfeed-inbox-footer-link"
              href={footer.href}
              onClick={(event) => {
                if (
                  event.button !== 0 ||
                  event.metaKey ||
                  event.ctrlKey ||
                  event.shiftKey ||
                  event.altKey
                )
                  return;
                event.preventDefault();
                setOpen(false);
                onNavigate(footer.href);
              }}
            >
              {footer.label}
            </a>
          )}
        />
      </Panel>
    </Popover.Root>
  );
}
