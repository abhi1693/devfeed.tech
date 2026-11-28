import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { markdownParseCache } from "./markdown-cache";

/** Untrusted publisher/AI text: no raw HTML, scripts, remote badges or images. */
export function Markdown({
  children,
  compact = false,
  className = "",
}: {
  children: string;
  compact?: boolean;
  className?: string;
}) {
  return (
    <div className={`markdown${compact ? " markdown-compact" : ""} ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, markdownParseCache.plugin]}
        skipHtml
        components={{
          img: () => null,
          a: ({ children, href }) =>
            compact ? (
              <span>{children}</span>
            ) : href ? (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            ) : (
              <span>{children}</span>
            ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
