"use client";

import { useEffect, useRef, useState } from "react";
import { CopyButton, type CopyStatus } from "@/components/copy-button";
import { readerRequest } from "@/lib/reader-runtime";
import { Tabs } from "radix-ui";
import {
  ArrowUpRight,
  Check,
  ChevronDown,
  CodeXml,
  Plug,
  SquareTerminal,
  UserRound,
} from "lucide-react";
import { siClaude, siCursor } from "simple-icons";
import {
  mcpAgentPrompt,
  mcpClients,
  mcpConfiguration,
  mcpEndpoint,
  mcpExamplePrompt,
  mcpServerGuide,
  type McpClient,
} from "@/lib/mcp";

const tools = [
  ["search", "Search articles, topics, sources, and tags."],
  ["get_article", "Get an article preview and publisher links."],
  ["get_feed", "Browse articles by topic, source, language, and content type."],
  ["list_topics", "List and filter developer topics."],
  ["list_sources", "Discover approved publications and sources."],
  ["get_source", "Get a publication’s public profile."],
];

function ClientIcon({ client }: { client: McpClient }) {
  if (client === "claude" || client === "cursor")
    return (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path d={client === "claude" ? siClaude.path : siCursor.path} />
      </svg>
    );
  const Icon = client === "vscode" ? CodeXml : client === "codex" ? SquareTerminal : Plug;
  return <Icon size={22} aria-hidden="true" />;
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
    <article className="mcp-page">
      <header className="mcp-page-header">
        <div>
          <p className="mcp-kicker">INTEGRATIONS / MCP</p>
          <h1>Connect your agent</h1>
          <p>Access DevFeed’s developer articles, topics, and sources from your AI tools.</p>
        </div>
        <a className="mcp-doc-link" href={mcpServerGuide} target="_blank" rel="noopener noreferrer">
          Server documentation <ArrowUpRight size={14} aria-hidden="true" />
        </a>
      </header>

      <section className="mcp-server" aria-label="Server connection">
        <div className="mcp-server-heading">
          <label htmlFor="mcp-endpoint">MCP server URL</label>
          <span>
            Streamable HTTP <span aria-hidden="true">·</span> Read-only
          </span>
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
            aria-describedby="mcp-endpoint-help"
            onChange={(event) => {
              edited.current = true;
              setEndpoint(event.target.value);
            }}
          />
          {url && <McpCopyButton key={url} text={url} label="Copy address" />}
        </div>
        <p id="mcp-endpoint-help" className={endpoint.trim() && !url ? "mcp-error" : ""}>
          {url ? (
            "Use a server address reachable from the machine running your agent. You can replace the deployment’s default URL."
          ) : endpoint.trim() ? (
            "Enter an HTTP or HTTPS URL without embedded credentials or a fragment."
          ) : configState === "loading" ? (
            "Loading this deployment’s MCP URL…"
          ) : configState === "error" ? (
            "Couldn’t load this deployment’s MCP URL. Enter your server URL to continue."
          ) : (
            <>
              This deployment hasn’t configured an MCP URL. Enter your server URL or{" "}
              <a href={mcpServerGuide} target="_blank" rel="noopener noreferrer">
                set up a server <ArrowUpRight size={12} aria-hidden="true" />
              </a>
              .
            </>
          )}
        </p>
      </section>

      <Tabs.Root defaultValue="human" className="mcp-workspace">
        <Tabs.List className="mcp-tabs" aria-label="Connection method">
          <Tabs.Trigger value="human">
            <UserRound size={16} aria-hidden="true" />
            I&apos;m a Human
          </Tabs.Trigger>
          <Tabs.Trigger value="agent">
            <SquareTerminal size={16} aria-hidden="true" />
            I&apos;m an Agent
          </Tabs.Trigger>
        </Tabs.List>
        <div className="mcp-client-picker">
          <fieldset>
            <legend>Choose your tool</legend>
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
                        <small>{item.description}</small>
                      </span>
                      <Check className="mcp-client-check" size={14} aria-hidden="true" />
                    </span>
                  </label>
                ),
              )}
            </div>
          </fieldset>
        </div>
        <Tabs.Content value="human" className="mcp-panel">
          <div className="mcp-panel-heading">
            <div>
              <h2>{selected.name} setup</h2>
              <p>Configure the connection manually.</p>
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
              {selected.steps.map(({ title, description }, index) => (
                <li key={`${client}:${title}`}>
                  <span className="mcp-step-number" aria-hidden="true">
                    {index + 1}
                  </span>
                  <div>
                    <h3>{title}</h3>
                    <p>{description}</p>
                  </div>
                </li>
              ))}
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
              <p className="mcp-config-note">
                The server must be running and reachable from your agent’s environment.
              </p>
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
              <h2>Let your agent configure the connection</h2>
              <p>
                Paste this prompt into {selected.name}. It includes your server URL and setup
                instructions.
              </p>
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
          <p className="mcp-config-note">
            An agent with access to your configuration can apply the change. Other agents can walk
            you through the manual steps.
          </p>
        </Tabs.Content>
      </Tabs.Root>

      <details className="mcp-reference">
        <summary>
          <span>
            Available tools <small>6 tools</small>
          </span>
          <ChevronDown size={16} aria-hidden="true" />
        </summary>
        <div className="mcp-reference-content">
          <p>
            Public metadata, article previews, and publisher links. Personal feeds and bookmarks are
            not exposed.
          </p>
          <table>
            <caption className="sr-only">DevFeed MCP tools</caption>
            <thead>
              <tr>
                <th scope="col">Tool</th>
                <th scope="col">Description</th>
              </tr>
            </thead>
            <tbody>
              {tools.map(([name, description]) => (
                <tr key={name}>
                  <th scope="row">
                    <code>{name}</code>
                  </th>
                  <td>{description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </article>
  );
}
