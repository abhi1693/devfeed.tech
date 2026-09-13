"use client";

import { useEffect, useRef, useState } from "react";
import { Popover } from "radix-ui";
import { Check, Link2, Share2 } from "lucide-react";

export function articleShareLinks(url: string, title: string) {
  return [
    {
      name: "Reddit",
      href: `https://www.reddit.com/submit?${new URLSearchParams({ url, title })}`,
    },
    { name: "X", href: `https://x.com/share?${new URLSearchParams({ url, text: title })}` },
    {
      name: "LinkedIn",
      href: `https://www.linkedin.com/shareArticle?${new URLSearchParams({ url, title })}`,
    },
  ];
}

function ShareIcon({ name }: { name: string }) {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" focusable="false">
      {name === "X" ? (
        <path
          fill="currentColor"
          d="M18.9 2H22l-6.8 7.8L23.2 22h-6.3L12 14.6 5.5 22H2.3l8.2-9.4L.8 2h6.5l4.5 6.8L18.9 2Zm-1.1 18h1.7L6.2 3.9H4.4L17.8 20Z"
        />
      ) : name === "LinkedIn" ? (
        <>
          <rect x="1" y="1" width="22" height="22" rx="3" fill="#0a66c2" />
          <circle cx="6.3" cy="6.5" r="1.5" fill="white" />
          <path
            fill="white"
            d="M5 9h2.6v10H5zm5 0h2.5v1.4C13.1 9.4 14.1 9 15.4 9c2.7 0 3.6 1.6 3.6 4.5V19h-2.7v-5c0-1.5-.3-2.5-1.7-2.5-1.5 0-1.9 1.1-1.9 2.5v5H10Z"
          />
        </>
      ) : (
        <g stroke="#ff4500" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="m12 8 1.5-5 4 1" fill="none" />
          <circle cx="19" cy="4.4" r="1.6" fill="white" />
          <circle cx="4" cy="11" r="2.2" fill="white" />
          <circle cx="20" cy="11" r="2.2" fill="white" />
          <ellipse cx="12" cy="14" rx="9" ry="6.5" fill="white" />
          <circle cx="8.5" cy="13" r="1" fill="#ff4500" />
          <circle cx="15.5" cy="13" r="1" fill="#ff4500" />
          <path d="M8.5 17q3.5 2 7 0" fill="none" />
        </g>
      )}
    </svg>
  );
}

export function ArticleShare({
  slug,
  title,
  label = false,
}: {
  slug: string;
  title: string;
  label?: boolean;
}) {
  const trigger = useRef<HTMLButtonElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const operation = useRef(0);
  const [open, setOpen] = useState(false);
  const [container, setContainer] = useState<HTMLElement | null>(null);
  const [url, setUrl] = useState("");
  const [status, setStatus] = useState<"idle" | "copying" | "copied" | "failed">("idle");
  useEffect(() => {
    const token = operation;
    return () => {
      token.current++;
    };
  }, []);
  useEffect(() => {
    if (status === "failed") {
      input.current?.focus();
      input.current?.select();
    }
  }, [status]);
  async function copy() {
    if (status === "copying") return;
    const token = ++operation.current;
    setStatus("copying");
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(url);
      if (operation.current === token) setStatus("copied");
    } catch {
      if (operation.current !== token) return;
      // Local HTTP origins may lack the Clipboard API. Keep the selection inside
      // the popover so native dialogs and focus scopes permit the fallback.
      const field = document.createElement("textarea");
      const focused = document.activeElement;
      field.value = url;
      field.readOnly = true;
      field.style.cssText = "position:fixed;opacity:0;pointer-events:none;width:1px;height:1px";
      panel.current?.appendChild(field);
      let copied = false;
      try {
        field.focus();
        field.select();
        copied = document.execCommand("copy");
      } catch {
        // Some browsers also deny the legacy copy command; retain manual copy.
      } finally {
        field.remove();
        if (focused instanceof HTMLElement) focused.focus();
      }
      setStatus(copied ? "copied" : "failed");
    }
  }
  return (
    <Popover.Root
      open={open}
      onOpenChange={(value) => {
        operation.current++;
        setStatus("idle");
        if (value) {
          setUrl(new URL(`/articles/${encodeURIComponent(slug)}`, window.location.origin).href);
          // A portal outside a native dialog is inert and below its top layer.
          setContainer(trigger.current?.closest("dialog") ?? document.body);
        }
        setOpen(value);
      }}
    >
      <Popover.Trigger asChild>
        <button
          ref={trigger}
          type="button"
          className={`article-share-trigger${label ? " button" : ""}`}
          aria-label={`Share article: ${title}`}
          title="Share article"
        >
          <Share2 size={16} aria-hidden="true" />
          {label && <span className="article-share-label">Share</span>}
        </button>
      </Popover.Trigger>
      <Popover.Portal container={container}>
        <Popover.Content
          ref={panel}
          className="article-share-popover"
          side="top"
          align="end"
          sideOffset={8}
          collisionPadding={12}
          aria-label="Share article"
          onKeyDown={(event) => {
            if (event.key === "ArrowLeft" || event.key === "ArrowRight") event.stopPropagation();
          }}
        >
          <p className="article-share-heading">Share this article</p>
          <div className="article-share-options">
            <button
              type="button"
              onClick={copy}
              disabled={status === "copying"}
              aria-label="Copy link"
            >
              <span className="article-share-icon">
                {status === "copied" ? (
                  <Check size={22} aria-hidden="true" />
                ) : (
                  <Link2 size={22} aria-hidden="true" />
                )}
              </span>
              <span>{status === "copied" ? "Copied!" : "Copy link"}</span>
            </button>
            {articleShareLinks(url, title).map((option) => (
              <a
                key={option.name}
                href={option.href}
                target="_blank"
                rel="noopener noreferrer"
                aria-label={`Share on ${option.name} (opens in a new tab)`}
              >
                <span className="article-share-icon">
                  <ShareIcon name={option.name} />
                </span>
                <span>{option.name}</span>
              </a>
            ))}
          </div>
          <p className="article-share-status" role="status">
            {status === "copied"
              ? "Link copied."
              : status === "failed"
                ? "Couldn’t copy automatically. Select and copy the link below."
                : ""}
          </p>
          {status === "failed" && (
            <input
              ref={input}
              aria-label="Article link"
              readOnly
              value={url}
              onFocus={(event) => event.currentTarget.select()}
            />
          )}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
