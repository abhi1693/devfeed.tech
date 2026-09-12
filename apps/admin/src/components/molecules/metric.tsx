import Link from "next/link";
import type { ReactNode } from "react";
import { Card, CardContent } from "@/components/atoms/card";

export function Metric({
  label,
  value,
  description,
  icon,
  href,
}: {
  label: string;
  value: number;
  description?: string;
  icon?: ReactNode;
  href?: string;
}) {
  const content = (
    <Card className="h-full gap-0 py-5 shadow-none transition-colors group-hover:border-foreground/25">
      <CardContent className="px-5">
        <div className="flex min-h-10 items-center justify-between gap-2 sm:min-h-5">
          <p className="text-sm text-muted-foreground">{label}</p>
          {icon && (
            <span aria-hidden className="text-muted-foreground [&>svg]:size-4">
              {icon}
            </span>
          )}
        </div>
        <p className="mt-3 text-3xl font-semibold tracking-tight tabular-nums">
          {value.toLocaleString("en")}
        </p>
        {description && (
          <p className="mt-2 text-xs leading-5 text-muted-foreground">{description}</p>
        )}
      </CardContent>
    </Card>
  );
  return href ? (
    <Link
      href={href}
      className="group min-w-0 rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {content}
    </Link>
  ) : (
    content
  );
}
