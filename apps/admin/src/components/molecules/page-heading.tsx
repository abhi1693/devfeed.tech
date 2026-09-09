import Link from "next/link";
import { PageTitle } from "./page-title";
export function PageHeading({ title, browserTitle = title, description, trail = [], leading, children }: { title: string; browserTitle?: string; description?: string; trail?: { label: string; href: string }[]; leading?: React.ReactNode; children?: React.ReactNode }) {
  return <div className="space-y-3">
    <PageTitle title={browserTitle} />
    <nav aria-label="Breadcrumb" className="flex flex-wrap gap-2 text-xs text-muted-foreground"><Link href="/" prefetch={false} className="hover:underline">Home</Link>{trail.map(item => <span key={item.href} className="flex gap-2"><span aria-hidden>/</span><Link href={item.href} prefetch={false} className="hover:underline">{item.label}</Link></span>)}</nav>
    <div className="flex flex-wrap items-start justify-between gap-4"><div className="flex min-w-0 max-w-full items-center gap-3">{leading && <div className="shrink-0">{leading}</div>}<div className="min-w-0"><h1 className="break-words text-2xl font-semibold tracking-tight">{title}</h1>{description && <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{description}</p>}</div></div><div className="flex flex-wrap gap-2">{children}</div></div>
  </div>;
}
