import Link from "next/link";

export function SummaryStrip({
  items,
}: {
  items: { label: string; value: number; href?: string; description?: string }[];
}) {
  return (
    <div className="grid grid-cols-2 overflow-hidden rounded-lg border bg-card sm:grid-cols-4">
      {items.map((item) => {
        const content = (
          <>
            <span className="text-xs text-muted-foreground">{item.label}</span>
            <span className="text-xl font-semibold tabular-nums">
              {item.value.toLocaleString("en")}
            </span>
          </>
        );
        const className =
          "flex min-w-0 items-center justify-between gap-3 border-r border-b px-4 py-4 last:border-r-0 [&:nth-child(2)]:border-r-0 [&:nth-child(n+3)]:border-b-0 sm:border-b-0 sm:[&:nth-child(2)]:border-r";
        return item.href ? (
          <Link
            key={item.label}
            href={item.href}
            aria-label={`${item.label} ${item.value.toLocaleString("en")}`}
            title={item.description}
            className={`${className} hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-ring`}
          >
            {content}
          </Link>
        ) : (
          <div key={item.label} title={item.description} className={className}>
            {content}
          </div>
        );
      })}
    </div>
  );
}
