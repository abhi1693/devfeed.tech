"use client";

import { useEffect, useState } from "react";
import { AccountGate, useUser } from "@/components/user-account";
import { userRequest } from "@/lib/user";

type RequestDetails = {
  client_name: string;
  scopes: string[];
  resource: string;
  redirect_uri: string;
};

export function McpConsent({ requestId }: { requestId: string }) {
  const { user, loading } = useUser();
  const [details, setDetails] = useState<RequestDetails | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!user) return;
    const controller = new AbortController();
    userRequest<RequestDetails>(`mcp/requests/${encodeURIComponent(requestId)}`, {
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted) setDetails(value);
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setError(
            "This connection request expired or is unavailable. Restart the connection in your agent.",
          );
      });
    return () => controller.abort();
  }, [user, requestId]);
  if (loading) return <p role="status">Checking sign-in…</p>;
  if (!user)
    return (
      <AccountGate
        title="Sign in to connect your agent"
        returnTo={`/mcp/authorize?${new URLSearchParams({ request: requestId })}`}
      >
        {null}
      </AccountGate>
    );
  async function respond(approved: boolean) {
    if (!user) return;
    setBusy(true);
    try {
      const result = await userRequest<{ redirect_url: string }>(
        `mcp/requests/${encodeURIComponent(requestId)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": user.csrf_token },
          body: JSON.stringify({ approved }),
        },
      );
      window.location.assign(result.redirect_url);
    } catch {
      setError("Couldn’t authorize this connection. Restart the connection in your agent.");
      setBusy(false);
    }
  }
  return (
    <section className="mcp-page" aria-label="Agent authorization">
      <h1>Connect an agent to your account</h1>
      {error && <p role="alert">{error}</p>}
      {details && (
        <>
          <h2>{details.client_name}</h2>
          <p>This client name is supplied by the app. Only approve a connection you started.</p>
          <p>Signed in as {user.name || user.email || "your DevFeed account"}.</p>
          <ul>
            <li>Read your personal feed, bookmarks, and followed topics and sources.</li>
            {details.scopes.includes("devfeed:write") && (
              <li>
                Save or remove bookmarks, follow or unfollow topics and sources, and like or unlike
                articles.
              </li>
            )}
          </ul>
          <p>
            Server: <code>{details.resource}</code>
          </p>
          <p>
            Return to: <code>{details.redirect_uri}</code>
          </p>
          <p>
            You can disconnect this agent from the MCP setup page at any time. Your client can renew
            automatically while the connection remains active, up to its maximum lifetime.
          </p>
          <div className="mcp-consent-actions">
            <button
              className="button primary"
              type="button"
              disabled={busy}
              onClick={() => void respond(true)}
            >
              Allow connection
            </button>
            <button
              className="button"
              type="button"
              disabled={busy}
              onClick={() => void respond(false)}
            >
              Cancel
            </button>
          </div>
        </>
      )}
    </section>
  );
}
