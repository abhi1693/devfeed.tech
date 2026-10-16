"use client";

import type { AdLinkRel } from "./ad-link-rel";

const descriptions: Record<AdLinkRel, string> = {
  nofollow: "Ask search engines not to follow the link.",
  sponsored: "Identify a paid placement.",
  ugc: "Identify user-generated content.",
  external: "Identify a link to another website.",
};

export function AdLinkRelSelector({
  value,
  options,
  onChange,
  configurable,
  disabled = false,
  label = "Link relationship",
}: {
  value: readonly AdLinkRel[];
  options: readonly AdLinkRel[];
  onChange: (value: AdLinkRel[]) => void;
  configurable: boolean;
  disabled?: boolean;
  label?: string;
}) {
  return (
    <fieldset disabled={disabled} className="min-w-0 space-y-2">
      <legend className="mb-2 text-sm font-medium">{label}</legend>
      {configurable ? (
        <>
          <div className="flex flex-wrap gap-2">
            {options.map((token) => (
              <label
                key={token}
                title={descriptions[token]}
                className={`inline-flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-sm transition-colors ${value.includes(token) ? "border-foreground/30 bg-accent text-accent-foreground" : "hover:bg-muted"}`}
              >
                <input
                  type="checkbox"
                  checked={value.includes(token)}
                  onChange={(event) =>
                    onChange(
                      options.filter((option) =>
                        option === token ? event.target.checked : value.includes(option),
                      ),
                    )
                  }
                />
                <span className="font-mono text-xs">{token}</span>
              </label>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            Clear all for a standard link.{" "}
            <a
              href="https://developers.google.com/search/docs/crawling-indexing/qualify-outbound-links"
              target="_blank"
              rel="noopener noreferrer"
              className="underline underline-offset-2"
            >
              Google recommends sponsored for paid links.
            </a>
          </p>
        </>
      ) : (
        <p className="text-sm text-muted-foreground">
          <code className="mr-2 rounded bg-muted px-2 py-1 text-foreground">nofollow</code>
          Fixed for this tier.
        </p>
      )}
    </fieldset>
  );
}
