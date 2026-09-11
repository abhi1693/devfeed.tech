"use client";

import Link from "next/link";
import { Bell, CheckCheck, X } from "lucide-react";
import {
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { useUser } from "./user-account";
import { userRequest, type UserIdentity } from "@/lib/user";
import {
  createInboxClient,
  notificationArticle,
  type InboxConfig,
} from "@/lib/inbox";

export function NotificationInbox() {
  const { user } = useUser();
  return user ? <UserInbox key={user.user_id} user={user} /> : null;
}

function UserInbox({ user }: { user: UserIdentity }) {
  const [config, setConfig] = useState<InboxConfig>();
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    userRequest<InboxConfig>("notifications/config", {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
    })
      .then((value) => {
        if (!controller.signal.aborted) {
          setConfig(value);
          setError(false);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setError(true);
      });
    return () => controller.abort();
  }, [retry]);
  if (config?.enabled)
    return (
      <ConnectedInbox
        key={user.csrf_token}
        config={config}
        csrf={user.csrf_token}
      />
    );
  if (error)
    return (
      <button
        className="inbox-bell"
        aria-label="Notifications unavailable. Retry"
        title="Retry notifications"
        onClick={() => setRetry((value) => value + 1)}
      >
        <Bell size={19} />
        <span className="inbox-dot" />
      </button>
    );
  return null;
}

function ConnectedInbox({
  config,
  csrf,
}: {
  config: InboxConfig;
  csrf: string;
}) {
  const [client] = useState(() => createInboxClient(config, csrf));
  const state = useSyncExternalStore(
    (listener) => client.subscribe(listener),
    () => client.getSnapshot(),
    () => client.getSnapshot(),
  );
  const panel = useRef<HTMLDivElement>(null);
  const id = useId();
  useEffect(() => {
    const sync = () => {
      if (document.visibilityState === "hidden") client.close();
      else client.connect();
    };
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => {
      document.removeEventListener("visibilitychange", sync);
      client.close();
    };
  }, [client]);
  const unread = state.counts.unread;
  return (
    <>
      <button
        className="inbox-bell"
        type="button"
        popoverTarget={id}
        aria-haspopup="dialog"
        aria-label={`Notifications${unread ? `, ${unread} unread` : ""}`}
        title="Notifications"
        onClick={() => {
          void client.markAllSeen();
        }}
      >
        <Bell size={19} aria-hidden="true" />
        {unread > 0 && (
          <span className="inbox-count" aria-hidden="true">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>
      <div
        id={id}
        ref={panel}
        popover="auto"
        className="user-inbox"
        role="dialog"
        aria-label="Notifications"
      >
        <div className="inbox-heading">
          <h2>Notifications</h2>
          <button
            className="inbox-close"
            popoverTarget={id}
            popoverTargetAction="hide"
            aria-label="Close notifications"
          >
            <X size={18} />
          </button>
        </div>
        <div className="inbox-toolbar">
          <span>From your topics</span>
          <button
            disabled={!unread}
            onClick={() => {
              void client.markAllRead();
            }}
          >
            <CheckCheck size={16} aria-hidden="true" />
            Mark all read
          </button>
        </div>
        {state.error && (
          <p className="inbox-status" role="status">
            Couldn’t load notifications.{" "}
            <button
              onClick={() => {
                void client.refresh();
              }}
            >
              Retry
            </button>
          </p>
        )}
        {!state.items.length && !state.error && (
          <p className="inbox-status" role="status">
            {state.isLoading
              ? "Loading notifications…"
              : "You’re all caught up. New articles from topics you follow will appear here."}
          </p>
        )}
        <ul className="inbox-items">
          {state.items.map((item) => {
            const href = notificationArticle(item.payload.action_url);
            const content = (
              <>
                <span className="inbox-item-title">
                  {String(item.payload.title ?? "New article")}
                </span>
                <span className="inbox-item-body">
                  {String(item.payload.body ?? "")}
                </span>
                <time dateTime={item.occurredAt}>
                  {new Date(item.occurredAt).toLocaleDateString(undefined, {
                    month: "short",
                    day: "numeric",
                  })}
                </time>
              </>
            );
            return (
              <li key={item.id} className={item.read ? "" : "is-unread"}>
                {href ? (
                  <Link
                    href={href}
                    scroll={false}
                    prefetch={false}
                    onClick={() => {
                      void client.markRead(item);
                      panel.current?.hidePopover();
                    }}
                  >
                    {content}
                  </Link>
                ) : (
                  <button
                    onClick={() => {
                      void client.markRead(item);
                    }}
                  >
                    {content}
                  </button>
                )}
              </li>
            );
          })}
        </ul>
        {state.hasMore && (
          <button
            className="inbox-more"
            disabled={state.isLoading}
            onClick={() => {
              void client.fetchMore();
            }}
          >
            Load more
          </button>
        )}
      </div>
    </>
  );
}
