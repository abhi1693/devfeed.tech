"use client";
import { useEffect, useState } from "react";
import { useUser } from "@/components/user-account";
import { userRequest } from "@/lib/user";

type Connection = { id: string; client_name: string; scopes: string[]; expires_at: number };
export function McpConnections() {
  const { user } = useUser();
  const [connections, setConnections] = useState<{ owner: string; items: Connection[] } | null>(
    null,
  );
  const items = connections && connections.owner === user?.user_id ? connections.items : [];
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!user) return;
    const controller = new AbortController();
    userRequest<{ items: Connection[] }>("mcp/connections", { signal: controller.signal })
      .then((value) => {
        if (!controller.signal.aborted) setConnections({ owner: user.user_id, items: value.items });
      })
      .catch(() => {
        if (!controller.signal.aborted) setMessage("Couldn’t load connected agents.");
      });
    return () => controller.abort();
  }, [user]);
  if (!user) return null;
  return (
    <section className="mcp-server" aria-label="Connected agents">
      <h2>Connected agents</h2>
      <p>Disconnecting an agent immediately removes its access to your account.</p>
      {!items.length && !message && <p>No connected agents.</p>}
      <ul>
        {items.map((item) => (
          <li key={item.id}>
            <strong>{item.client_name}</strong> ·{" "}
            {item.scopes.includes("devfeed:write") ? "Read and write" : "Read-only"} · Expires
            without renewal {new Date(item.expires_at * 1000).toLocaleDateString()}
            <button
              className="button"
              type="button"
              disabled={busy}
              aria-label={`Disconnect ${item.client_name}`}
              onClick={async () => {
                setBusy(true);
                try {
                  await userRequest(`mcp/connections/${item.id}`, {
                    method: "DELETE",
                    headers: { "X-CSRF-Token": user.csrf_token },
                  });
                  setConnections({
                    owner: user.user_id,
                    items: items.filter((value) => value.id !== item.id),
                  });
                  setMessage("Agent disconnected.");
                } catch {
                  setMessage("Couldn’t disconnect this agent. Please retry.");
                } finally {
                  setBusy(false);
                }
              }}
            >
              Disconnect
            </button>
          </li>
        ))}
      </ul>
      <p role="status">{message}</p>
    </section>
  );
}
