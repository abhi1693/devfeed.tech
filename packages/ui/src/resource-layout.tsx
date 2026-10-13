"use client";

import { useEffect, type ReactNode } from "react";
import Link from "next/link";

export function ResourceHeading({
  title,
  description,
  trail = [],
  homeHref = "/",
  homeLabel = "Home",
  browserTitle = title,
  siteTitle = "DevFeed Admin",
  leading,
  children,
}: {
  title: string;
  description?: string;
  trail?: { label: string; href: string }[];
  homeHref?: string;
  homeLabel?: string;
  browserTitle?: string;
  siteTitle?: string;
  leading?: ReactNode;
  children?: ReactNode;
}) {
  useEffect(() => {
    const previous = document.title;
    const current = `${browserTitle.replace(/\s+/g, " ").trim()} · ${siteTitle}`;
    document.title = current;
    return () => {
      if (document.title === current) document.title = previous;
    };
  }, [browserTitle, siteTitle]);
  return (
    <div className="space-y-3">
      <nav aria-label="Breadcrumb" className="flex flex-wrap gap-2 text-xs text-muted-foreground">
        <Link href={homeHref} prefetch={false} className="hover:underline">
          {homeLabel}
        </Link>
        {trail.map((item) => (
          <span key={item.href} className="flex gap-2">
            <span aria-hidden>/</span>
            <Link href={item.href} prefetch={false} className="hover:underline">
              {item.label}
            </Link>
          </span>
        ))}
      </nav>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 max-w-full items-center gap-3">
          {leading && <div className="shrink-0">{leading}</div>}
          <div className="min-w-0">
            <h1 className="break-words text-2xl font-semibold tracking-tight">{title}</h1>
            {description && (
              <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{description}</p>
            )}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">{children}</div>
      </div>
    </div>
  );
}

export function ResourcePanel({
  title,
  fields,
  children,
}: {
  title: string;
  fields?: { label: string; value: ReactNode }[];
  children?: ReactNode;
}) {
  return (
    <section
      data-slot="card"
      className="flex min-w-0 flex-col gap-6 rounded-xl border bg-card py-6 text-card-foreground"
    >
      <div data-slot="card-header" className="border-b px-6 pb-6">
        <h2 data-slot="card-title" className="text-sm font-semibold leading-none">
          {title}
        </h2>
      </div>
      <div data-slot="card-content" className="px-6">
        {fields && (
          <dl className="divide-y">
            {fields.map((field) => (
              <div
                key={field.label}
                className="grid gap-1 py-3 text-sm sm:grid-cols-[160px_minmax(0,1fr)] sm:gap-4"
              >
                <dt className="text-muted-foreground">{field.label}</dt>
                <dd className="min-w-0">{field.value}</dd>
              </div>
            ))}
          </dl>
        )}
        {children}
      </div>
    </section>
  );
}

export function DataValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "")
    return <span className="text-muted-foreground">—</span>;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value))
    return value.length ? (
      <ul className="space-y-2">
        {value.map((item, index) => (
          <li key={index}>
            <DataValue value={item} />
          </li>
        ))}
      </ul>
    ) : (
      <span className="text-muted-foreground">None</span>
    );
  if (typeof value === "object")
    return (
      <dl className="space-y-2">
        {Object.entries(value).map(([key, item]) => (
          <div key={key}>
            <dt className="text-xs text-muted-foreground">
              {key.replaceAll("_", " ").replace(/^./, (char) => char.toUpperCase())}
            </dt>
            <dd>
              <DataValue value={item} />
            </dd>
          </div>
        ))}
      </dl>
    );
  const text = String(value);
  if (/^https?:\/\//.test(text))
    return (
      <a
        href={text}
        target="_blank"
        rel="noopener noreferrer"
        className="break-all text-blue-700 dark:text-blue-400 hover:underline"
      >
        {text}
      </a>
    );
  return <span className="whitespace-pre-wrap break-words">{text}</span>;
}
