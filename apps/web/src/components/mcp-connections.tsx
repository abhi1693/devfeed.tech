"use client";
import { useEffect, useRef, useState } from "react";
import { useUser } from "@/components/user-account";
import { userRequest } from "@/lib/user";
import { revokeMcpConnection } from "@/lib/user-mutations";

type Connection = { id: string; client_name: string; scopes: string[]; expires_at: number };
export function McpConnections() {
  const { user } = useUser();
  const [connections, setConnections] = useState<{
    owner: string;
    items: Connection[];
    error?: string;
  } | null>(null);
  const current = connections?.owner === user?.user_id ? connections : null;
  const items = current?.items ?? [];
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), [user?.user_id]);
  useEffect(() => {
    if (!user) return;
    const controller = new AbortController();
    userRequest<{ items: Connection[] }>("mcp/connections", { signal: controller.signal })
      .then((value) => {
        if (!controller.signal.aborted) setConnections({ owner: user.user_id, items: value.items });
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setConnections({
            owner: user.user_id,
            items: [],
            error: "Couldn’t load connected agents. Reopen this tab to try again.",
          });
      });
    return () => controller.abort();
  }, [user]);
  if (!user) return null;
  async function disconnect(item: Connection) {
    if (!user) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(item.id);
    setMessage("");
    try {
      await revokeMcpConnection(item.id, {
        csrfToken: user.csrf_token,
        signal: controller.signal,
      });
      if (controller.signal.aborted) return;
      setConnections((value) =>
        value?.owner === user.user_id
          ? {
              ...value,
              items: value.items.filter((connection) => connection.id !== item.id),
            }
          : value,
      );
      setMessage("Agent disconnected.");
    } catch {
      if (!controller.signal.aborted) setMessage("Couldn’t disconnect this agent. Please retry.");
    } finally {
      if (request.current === controller) {
        request.current = null;
        setBusy(null);
      }
    }
  }
  return (
    <section className="mcp-connections" aria-label="Connected agents">
      <header className="mcp-connections-heading">
        <h2>Connected agents</h2>
        {current && !current.error && (
          <span>
            {items.length} {items.length === 1 ? "connection" : "connections"}
          </span>
        )}
      </header>
      {!current && (
        <p role="status" className="mcp-connections-empty">
          Loading connected agents…
        </p>
      )}
      {current?.error && (
        <p role="alert" className="mcp-connections-empty mcp-error">
          {current.error}
        </p>
      )}
      {current && !current.error && !items.length && (
        <p className="mcp-connections-empty">No connected agents.</p>
      )}
      {items.length > 0 && (
        <>
          <div className="mcp-connection-columns" aria-hidden="true">
            <span>Agent</span>
            <span>Access</span>
            <span>Renew by</span>
            <span />
          </div>
          <ul className="mcp-connection-list">
            {items.map((item) => (
              <li key={item.id} className="mcp-connection-row">
                <strong className="mcp-connection-name">{item.client_name}</strong>
                <span className="mcp-connection-access">
                  {item.scopes.includes("devfeed:write") ? "Read and write" : "Read-only"}
                </span>
                <div className="mcp-connection-renewal">
                  <span className="sr-only">Renew by</span>
                  <time dateTime={new Date(item.expires_at * 1000).toISOString()}>
                    {new Date(item.expires_at * 1000).toLocaleDateString(undefined, {
                      year: "numeric",
                      month: "short",
                      day: "numeric",
                    })}
                  </time>
                </div>
                <button
                  className="button"
                  type="button"
                  disabled={busy !== null}
                  aria-label={`Disconnect ${item.client_name}`}
                  onClick={() => void disconnect(item)}
                >
                  {busy === item.id ? "Disconnecting…" : "Disconnect"}
                </button>
              </li>
            ))}
          </ul>
          <p className="mcp-connections-note">
            Disconnecting an agent immediately removes its account access.
          </p>
        </>
      )}
      {message && (
        <p role="status" className="mcp-connections-message">
          {message}
        </p>
      )}
    </section>
  );
}
