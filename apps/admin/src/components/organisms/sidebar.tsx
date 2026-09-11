"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { Menu, X, LayoutDashboard, FileText, Rss, Shapes, Tags, GitBranch, Activity, Network, Users } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { resourceHref } from "@/lib/routes";
import { resourceKeys, resources, type Resource } from "@/lib/resources";
const icons: Partial<Record<Resource, typeof FileText>> = { users: Users, articles: FileText, sources: Rss, topics: Shapes, tags: Tags, "topic-relations": GitBranch };
export function Sidebar() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const linkClass = (active: boolean) => `flex items-center gap-2.5 rounded-md px-3 py-2 text-sm ${active ? "bg-accent font-medium text-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground"}`;
  return <>
    <div className="border-b bg-card px-4 py-2 lg:hidden"><Button variant="outline" size="sm" aria-expanded={open} aria-controls="admin-sidebar" onClick={() => setOpen(!open)}>{open ? <X /> : <Menu />} Navigation</Button></div>
    <aside id="admin-sidebar" className={`${open ? "block" : "hidden"} shrink-0 border-b bg-card p-4 lg:sticky lg:top-0 lg:block lg:h-[calc(100dvh-65px)] lg:w-60 lg:overflow-y-auto lg:border-r lg:border-b-0`}>
      <nav aria-label="Administration">
        <Link href="/" prefetch={false} aria-current={pathname === "/" ? "page" : undefined} className={linkClass(pathname === "/")} onClick={() => setOpen(false)}><LayoutDashboard size={16} />Overview</Link>
        <Link href="/knowledge/graph" prefetch={false} aria-current={pathname.startsWith("/knowledge/") ? "page" : undefined} className={linkClass(pathname.startsWith("/knowledge/"))} onClick={() => setOpen(false)}><Network size={16} />Knowledge graph</Link>
        {["Content", "Taxonomy", "Audience", "Operations"].map(group => <section key={group} className="mt-6" aria-label={group}>
          <h2 className="mb-2 px-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">{group}</h2>
          <ul className="space-y-0.5">{group === "Operations" && [{ label: "Workers", href: "/workers" }, { label: "Queues", href: "/queues" }].map(item => <li key={item.href}><Link href={item.href} prefetch={false} aria-current={pathname === item.href || pathname.startsWith(`${item.href}/`) ? "page" : undefined} className={linkClass(pathname === item.href || pathname.startsWith(`${item.href}/`))} onClick={() => setOpen(false)}><Activity size={16} />{item.label}</Link></li>)}{resourceKeys.filter(key => resources[key].group === group).map(key => { const Icon = icons[key] ?? Activity; const active = pathname === resourceHref(key) || pathname.startsWith(`${resourceHref(key)}/`); return <li key={key}><Link href={resourceHref(key)} prefetch={false} aria-current={active ? "page" : undefined} onClick={() => setOpen(false)} className={linkClass(active)}><Icon size={16} className="shrink-0" />{resources[key].label}</Link></li>; })}</ul>
        </section>)}
      </nav>
    </aside>
  </>;
}
