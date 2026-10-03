"use client";

import { useEffect, useRef, useState } from "react";
import { CopyButton, type CopyStatus } from "@/components/copy-button";
import { McpConnections } from "@/components/mcp-connections";
import { useUser } from "@/components/user-account";
import { readerRequest } from "@/lib/reader-runtime";
import { Tabs } from "radix-ui";
import { ArrowUpRight, Bot, ChevronDown, Plug, SquareTerminal, UserRound } from "lucide-react";
import { siClaude } from "simple-icons";
import Image from "next/image";
import {
  mcpAgentPrompt,
  mcpClients,
  mcpConfiguration,
  mcpEndpoint,
  mcpExamplePrompt,
  type McpClient,
} from "@/lib/mcp";

const tools = [
  ["search", "Search articles, topics, sources, and tags.", "Public"],
  ["get_article", "Get an article preview and publisher links.", "Public"],
  ["get_feed", "Browse articles by topic, source, language, and content type.", "Public"],
  ["list_topics", "List and filter developer topics.", "Public"],
  ["list_sources", "Discover approved publications and sources.", "Public"],
  ["get_source", "Get a publication’s public profile.", "Public"],
  ["get_my_feed", "Read your personalized feed.", "Sign-in"],
  ["get_my_must_reads", "Read today’s five personalized Must Reads.", "Sign-in"],
  ["list_my_bookmarks", "Read your saved articles.", "Sign-in"],
  ["set_bookmark", "Save or remove a bookmark.", "Sign-in"],
  ["list_my_followed_topics", "Read your followed topics.", "Sign-in"],
  ["set_topic_follow", "Follow or unfollow one topic.", "Sign-in"],
  ["list_my_followed_sources", "Read your followed sources.", "Sign-in"],
  ["set_source_follow", "Follow or unfollow one source.", "Sign-in"],
  ["set_article_like", "Like or unlike an article.", "Sign-in"],
];

function ClientIcon({ client }: { client: McpClient }) {
  if (client === "other") return <Plug size={24} aria-hidden="true" />;
  if (client === "claude")
    return (
      <svg width="24" height="24" viewBox="0 0 24 24" fill={`#${siClaude.hex}`} aria-hidden="true">
        <path d={siClaude.path} />
      </svg>
    );
  return (
    <Image
      src={`/tool-icons/${client}.${client === "codex" ? "png" : "svg"}`}
      width={24}
      height={24}
      alt=""
      unoptimized
    />
  );
}

function McpCopyButton({ text, label }: { text: string; label: string }) {
  const [status, setStatus] = useState<CopyStatus>("idle");
  return (
    <div className="mcp-copy">
      <CopyButton text={text} label={label} onStatusChange={setStatus} />
      <span role="status" className={status === "failed" ? "mcp-copy-error" : "sr-only"}>
        {status === "copied"
          ? "Copied to clipboard."
          : status === "failed"
            ? "Couldn’t copy. Select the text and copy it manually."
            : ""}
      </span>
    </div>
  );
}

function CodeBlock({
  text,
  label,
  copyLabel,
  title,
}: {
  text: string;
  label: string;
  copyLabel: string;
  title: string;
}) {
  return (
    <div className="mcp-code-block">
      <div className="mcp-code-header">
        <span>{title}</span>
        <McpCopyButton key={text} text={text} label={copyLabel} />
      </div>
      <pre tabIndex={0} aria-label={label}>
        <code>{text}</code>
      </pre>
    </div>
  );
}

export function McpContent({ initialEndpoint }: { initialEndpoint?: string | null }) {
  const { user } = useUser();
  const [tab, setTab] = useState("human");
  const activeTab = tab === "connections" && !user ? "human" : tab;
  const [client, setClient] = useState<McpClient>("vscode");
  const [endpoint, setEndpoint] = useState(initialEndpoint ?? "");
  const [configState, setConfigState] = useState<"loading" | "ready" | "error">(
    initialEndpoint === undefined ? "loading" : "ready",
  );
  const edited = useRef(false);
  useEffect(() => {
    if (initialEndpoint !== undefined) return;
    const controller = new AbortController();
    void readerRequest("/api/v1/mcp/config", {
      cache: "no-store",
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("Configuration unavailable");
        const data: unknown = await response.json();
        if (!data || typeof data !== "object" || !("url" in data))
          throw new Error("Invalid configuration");
        const url =
          data.url === null ? null : typeof data.url === "string" ? mcpEndpoint(data.url) : null;
        if (data.url !== null && !url) throw new Error("Invalid configuration");
        if (controller.signal.aborted) return;
        if (!edited.current) setEndpoint(url ?? "");
        setConfigState("ready");
      })
      .catch(() => {
        if (!controller.signal.aborted) setConfigState("error");
      });
    return () => controller.abort();
  }, [initialEndpoint]);
  const url = mcpEndpoint(endpoint);
  const configuration = mcpConfiguration(client, endpoint);
  const prompt = mcpAgentPrompt(client, endpoint);
  const selected = mcpClients[client];
  return (
    <article className="mcp-page mcp-setup-page">
      <header className="mcp-page-header">
        <div>
          <h1>Connect your agent</h1>
        </div>
      </header>

      <section className="mcp-server" aria-label="Server connection">
        <div className="mcp-server-heading">
          <label htmlFor="mcp-endpoint">MCP server URL</label>
        </div>
        <div className="mcp-endpoint-row">
          <input
            id="mcp-endpoint"
            type="url"
            value={endpoint}
            spellCheck={false}
            autoComplete="off"
            placeholder="Enter your MCP server URL"
            aria-invalid={Boolean(endpoint.trim()) && !url}
            aria-describedby={!url ? "mcp-endpoint-help" : undefined}
            onChange={(event) => {
              edited.current = true;
              setEndpoint(event.target.value);
            }}
          />
          {url && <McpCopyButton key={url} text={url} label="Copy address" />}
        </div>
        {!url && (
          <p id="mcp-endpoint-help" className={endpoint.trim() ? "mcp-error" : ""}>
            {endpoint.trim()
              ? "Enter a valid HTTP or HTTPS URL."
              : configState === "loading"
                ? "Loading server URL…"
                : configState === "error"
                  ? "Couldn’t load the server URL. Enter it to continue."
                  : "Enter your MCP server URL to get started."}
          </p>
        )}
      </section>

      <div className="mcp-layout">
        <Tabs.Root value={activeTab} onValueChange={setTab} className="mcp-workspace">
          <Tabs.List className="mcp-tabs" aria-label="Connection method">
            <Tabs.Trigger value="human">
              <UserRound size={16} aria-hidden="true" />
              I&apos;m a Human
            </Tabs.Trigger>
            <Tabs.Trigger value="agent">
              <SquareTerminal size={16} aria-hidden="true" />
              I&apos;m an Agent
            </Tabs.Trigger>
            {user && (
              <Tabs.Trigger value="connections">
                <Bot size={16} aria-hidden="true" />
                Connected agents
              </Tabs.Trigger>
            )}
          </Tabs.List>
          {activeTab !== "connections" && (
            <div className="mcp-client-picker">
              <fieldset>
                <legend className="sr-only">MCP client</legend>
                <div className="mcp-clients">
                  {(Object.entries(mcpClients) as [McpClient, typeof selected][]).map(
                    ([value, item]) => (
                      <label key={value} className="mcp-client">
                        <input
                          type="radio"
                          name="mcp-client"
                          value={value}
                          checked={client === value}
                          onChange={() => setClient(value)}
                        />
                        <span className="mcp-client-option">
                          <ClientIcon client={value} />
                          <span>
                            <strong>{item.name}</strong>
                          </span>
                        </span>
                      </label>
                    ),
                  )}
                </div>
              </fieldset>
            </div>
          )}
          <Tabs.Content value="human" className="mcp-panel">
            <div className="mcp-panel-heading">
              <div>
                <h2>{selected.name} setup</h2>
              </div>
              {selected.docs && (
                <a
                  className="mcp-doc-link"
                  href={selected.docs}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Client documentation <ArrowUpRight size={14} aria-hidden="true" />
                </a>
              )}
            </div>
            <div className="mcp-manual-grid">
              <ol className="mcp-steps">
                {selected.steps.map((step, index) => (
                  <li key={`${client}:${step}`}>
                    <span className="mcp-step-number" aria-hidden="true">
                      {index + 1}
                    </span>
                    <p>{step}</p>
                  </li>
                ))}
                <li>
                  <span className="mcp-step-number" aria-hidden="true">
                    {selected.steps.length + 1}
                  </span>
                  <details key={client} className="mcp-sign-in">
                    <summary>
                      Sign in to DevFeed <ChevronDown size={14} aria-hidden="true" />
                    </summary>
                    <ol>
                      <li>
                        <p>{selected.signIn.instruction}</p>
                        {selected.signIn.command && (
                          <CodeBlock
                            text={selected.signIn.command}
                            title="Terminal"
                            label="Sign-in command"
                            copyLabel="Copy sign-in command"
                          />
                        )}
                      </li>
                      <li>
                        <p>
                          In the browser, sign in, review permissions, and select Allow connection.
                        </p>
                      </li>
                      <li>
                        <p>
                          Return to {selected.name} and ask it to show your saved DevFeed articles.
                        </p>
                      </li>
                    </ol>
                  </details>
                </li>
              </ol>
              <div>
                {configuration ? (
                  <CodeBlock
                    text={configuration}
                    title={selected.configurationLabel}
                    label="MCP configuration"
                    copyLabel={selected.copyLabel}
                  />
                ) : (
                  <p className="mcp-invalid">
                    Enter a valid server URL to generate the configuration.
                  </p>
                )}
              </div>
            </div>
            <div className="mcp-test-question">
              <div>
                <h3>Test the connection</h3>
                <p>{mcpExamplePrompt}</p>
              </div>
              <McpCopyButton text={mcpExamplePrompt} label="Copy question" />
            </div>
          </Tabs.Content>
          <Tabs.Content value="agent" className="mcp-panel">
            <div className="mcp-panel-heading">
              <div>
                <h2>Setup prompt</h2>
              </div>
            </div>
            {prompt ? (
              <div className="mcp-agent-prompt">
                <CodeBlock
                  text={prompt}
                  title={`Setup prompt · ${selected.name}`}
                  label="Agent setup prompt"
                  copyLabel="Copy setup prompt"
                />
              </div>
            ) : (
              <p className="mcp-invalid">Enter a valid server URL to generate the prompt.</p>
            )}
          </Tabs.Content>
          <Tabs.Content value="connections" className="mcp-panel">
            <McpConnections />
          </Tabs.Content>
        </Tabs.Root>

        <section className="mcp-reference" aria-labelledby="mcp-tools-heading">
          <div className="mcp-reference-heading">
            <h2 id="mcp-tools-heading">Available tools</h2>
            <span>{tools.length} tools</span>
          </div>
          <div className="mcp-reference-content">
            <table>
              <caption className="sr-only">DevFeed MCP tools</caption>
              <thead>
                <tr>
                  <th scope="col">Tool</th>
                  <th scope="col">Description</th>
                  <th scope="col">Access</th>
                </tr>
              </thead>
              <tbody>
                {tools.map(([name, description, access]) => (
                  <tr key={name}>
                    <th scope="row">
                      <code>{name}</code>
                    </th>
                    <td>{description}</td>
                    <td>{access}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </article>
  );
}
