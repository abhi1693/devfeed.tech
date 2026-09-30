"use client";

import { useEffect, useState } from "react";
import { BookOpen, Pencil, UserRound } from "lucide-react";
import Link from "@/components/reader-link";
import { AccountGate, useUser } from "@/components/user-account";
import { userRequest } from "@/lib/user";

type Access = "read-only" | "read-write";

type RequestDetails = {
  client_name: string;
  scopes: string[];
  resource: string;
};

export function McpConsent({ requestId }: { requestId: string }) {
  const { user, loading } = useUser();
  const owner = user?.user_id;
  const [loaded, setLoaded] = useState<{
    requestId: string;
    owner: string;
    details: RequestDetails;
  } | null>(null);
  const details = loaded?.requestId === requestId && loaded.owner === owner ? loaded.details : null;
  const [access, setAccess] = useState<Access>("read-write");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!owner) return;
    const controller = new AbortController();
    userRequest<RequestDetails>(`mcp/requests/${encodeURIComponent(requestId)}`, {
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted) {
          setLoaded({ requestId, owner, details: value });
          setAccess(value.scopes.includes("devfeed:write") ? "read-write" : "read-only");
          setError("");
        }
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setError(
            "This connection request expired or is unavailable. Restart the connection in your agent.",
          );
      });
    return () => controller.abort();
  }, [owner, requestId]);
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
    setError("");
    try {
      const result = await userRequest<{ redirect_url: string }>(
        `mcp/requests/${encodeURIComponent(requestId)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": user.csrf_token },
          body: JSON.stringify({ approved, ...(approved ? { access } : {}) }),
        },
      );
      window.location.assign(result.redirect_url);
    } catch {
      setError("Couldn’t authorize this connection. Restart the connection in your agent.");
      setBusy(false);
    }
  }
  return (
    <section className="mcp-page mcp-consent" aria-label="Agent authorization">
      <h1>Authorize connection</h1>
      <div className="mcp-consent-card" aria-busy={busy || (!details && !error)}>
        {error && (
          <p role="alert" className="mcp-consent-error">
            {error}
          </p>
        )}
        {!details && !error && (
          <p role="status" className="mcp-consent-state">
            Loading connection request…
          </p>
        )}
        {!details && error && (
          <div className="mcp-consent-state">
            <Link className="button" href="/mcp">
              Back to setup
            </Link>
          </div>
        )}
        {details && (
          <>
            <header className="mcp-consent-client">
              <div className="mcp-consent-client-heading">
                <h2>{details.client_name}</h2>
                <span className="mcp-consent-access">
                  {access === "read-write" ? "Read and write" : "Read-only"}
                </span>
              </div>
              <p>Client names are supplied by the app. Only approve a connection you started.</p>
            </header>
            <div className="mcp-consent-account">
              <UserRound size={20} aria-hidden="true" />
              <span>
                Signed in as <strong>{user.name || user.email || "your DevFeed account"}</strong>
              </span>
            </div>
            <fieldset className="mcp-consent-choice" disabled={busy}>
              <legend>Connection permissions</legend>
              <div className="mcp-consent-options">
                <label>
                  <input
                    type="radio"
                    name="mcp-access"
                    value="read-only"
                    checked={access === "read-only"}
                    onChange={() => setAccess("read-only")}
                  />
                  <span>Read-only</span>
                </label>
                <label>
                  <input
                    type="radio"
                    name="mcp-access"
                    value="read-write"
                    checked={access === "read-write"}
                    disabled={!details.scopes.includes("devfeed:write")}
                    onChange={() => setAccess("read-write")}
                  />
                  <span>Read-write</span>
                </label>
              </div>
              {!details.scopes.includes("devfeed:write") && (
                <p>This client requested read-only access.</p>
              )}
            </fieldset>
            <section className="mcp-consent-permissions" aria-labelledby="mcp-permissions-heading">
              <h3 id="mcp-permissions-heading">This agent can</h3>
              <ul>
                <li>
                  <BookOpen size={20} aria-hidden="true" />
                  <div>
                    <h4>Read your account</h4>
                    <p>Your personal feed, bookmarks, and followed topics and sources.</p>
                  </div>
                </li>
                {access === "read-write" && (
                  <li>
                    <Pencil size={20} aria-hidden="true" />
                    <div>
                      <h4>Update your account</h4>
                      <p>
                        Save or remove bookmarks, follow or unfollow topics and sources, and like or
                        unlike articles.
                      </p>
                    </div>
                  </li>
                )}
              </ul>
              <p className="mcp-consent-summary" aria-live="polite">
                {access === "read-only"
                  ? "Read-only access: this agent cannot change your bookmarks, follows, or likes."
                  : "Read-write access: this agent can read your account and update your bookmarks, follows, and likes."}
              </p>
            </section>
            <dl className="mcp-consent-destinations">
              <div>
                <dt>Server</dt>
                <dd>
                  <code>{details.resource}</code>
                </dd>
              </div>
            </dl>
            <footer className="mcp-consent-footer">
              <p>You can disconnect this agent from the MCP setup page at any time.</p>
              <div className="mcp-consent-actions">
                <button
                  className="button"
                  type="button"
                  disabled={busy}
                  onClick={() => void respond(false)}
                >
                  Cancel
                </button>
                <button
                  className="button primary"
                  type="button"
                  disabled={busy}
                  onClick={() => void respond(true)}
                >
                  Allow connection
                </button>
              </div>
            </footer>
          </>
        )}
      </div>
    </section>
  );
}
