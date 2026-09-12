import type { TopicProposalOutEvidenceItem } from "@/lib/api/generated/models";

export function TopicResearchEvidence({ evidence }: { evidence: TopicProposalOutEvidenceItem }) {
  return (
    <div className="space-y-3">
      <div>
        <p className="font-medium">AI research sources</p>
        <p className="text-xs text-muted-foreground">
          {Array.isArray(evidence.fields) && evidence.fields.join(", ")}
          {typeof evidence.model === "string" && ` · ${evidence.model}`}
        </p>
      </div>
      {Array.isArray(evidence.sources) &&
        evidence.sources.map((value, index) => {
          if (!value || typeof value !== "object") return null;
          const source = value as Record<string, unknown>;
          if (typeof source.url !== "string" || !/^https?:\/\//i.test(source.url)) return null;
          return (
            <div key={index} className="space-y-1">
              <a
                href={source.url}
                target="_blank"
                rel="noopener noreferrer"
                className="break-words text-primary hover:underline"
              >
                {typeof source.title === "string" ? source.title : source.url}
              </a>
              {typeof source.quote === "string" && (
                <blockquote className="border-l-2 pl-3 text-xs text-muted-foreground">
                  {source.quote}
                </blockquote>
              )}
            </div>
          );
        })}
    </div>
  );
}
