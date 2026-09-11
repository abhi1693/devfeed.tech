"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import type { Topic } from "@/lib/types";
import { userRequest, type Preferences } from "@/lib/user";
import { useUser, AccountGate } from "./user-account";
import { CatalogIcon } from "./catalog-icon";

function TopicChoices({ topics }: { topics: Topic[] }) {
  const { user, signOut } = useUser();
  const [selected, setSelected] = useState<string[] | null>(null);
  const [query, setQuery] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    userRequest<Preferences>("preferences", {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
    })
      .then((value) => setSelected(value.topic_ids))
      .catch(() => {
        if (!controller.signal.aborted)
          setMessage("Couldn’t load your topics. Reload to try again.");
      });
    return () => controller.abort();
  }, []);
  async function save() {
    setBusy(true);
    setMessage("");
    try {
      const result = await userRequest<Preferences>("preferences", {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": user!.csrf_token,
        },
        body: JSON.stringify({ topic_ids: selected }),
      });
      setSelected(result.topic_ids);
      setMessage("Your topics are saved.");
    } catch {
      setMessage(
        "Couldn’t save your topics. Try again, or sign in if your session expired.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function logout() {
    setBusy(true);
    try {
      await signOut();
    } catch {
      setMessage("Couldn’t sign out. Please try again.");
      setBusy(false);
    }
  }
  const visible = topics.filter((topic) =>
    topic.name.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Your topics</h1>
          <p>Choose up to 100 topics for your feed.</p>
        </div>
        <Link className="button" href="/my-feed">
          My feed
        </Link>
      </div>
      <label className="topic-search">
        Find a topic
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search topics"
        />
      </label>
      <p role="status">
        {message ||
          (selected === null
            ? "Loading your topics…"
            : `${selected.length} topics selected`)}
      </p>
      {selected !== null && (
        <>
          <div className="topic-choice-grid">
            {visible.map((topic) => (
              <label
                key={topic.id}
                className={`topic-choice ${selected.includes(topic.id) ? "selected" : ""}`}
              >
                <input
                  type="checkbox"
                  checked={selected.includes(topic.id)}
                  disabled={
                    busy ||
                    (!selected.includes(topic.id) && selected.length >= 100)
                  }
                  onChange={(e) =>
                    setSelected(
                      e.target.checked
                        ? [...selected, topic.id]
                        : selected.filter((id) => id !== topic.id),
                    )
                  }
                />
                <CatalogIcon url={topic.logo_url} />
                <span>{topic.name}</span>
              </label>
            ))}
          </div>
          {!visible.length && <p>No topics match your search.</p>}
          <div className="account-actions">
            <button className="button primary" disabled={busy} onClick={save}>
              {busy ? "Please wait…" : "Save topics"}
            </button>
            <button
              className="button"
              disabled={busy || !selected.length}
              onClick={() => setSelected([])}
            >
              Clear selection
            </button>
          </div>
        </>
      )}
      <button className="text-link" disabled={busy} onClick={logout}>
        Sign out
      </button>
    </>
  );
}
export function TopicPreferences({ topics }: { topics: Topic[] }) {
  return (
    <AccountGate>
      <TopicChoices topics={topics} />
    </AccountGate>
  );
}
