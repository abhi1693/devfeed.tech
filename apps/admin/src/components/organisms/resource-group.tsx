import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { PageHeading } from "@/components/molecules/page-heading";
import { resourceKeys, resources } from "@/lib/resources";
import { groupLabels, groupPaths, resourceHref, type RouteGroup } from "@/lib/routes";

export function ResourceGroup({ group }: { group: RouteGroup }) {
  const prefix = groupPaths[group];
  const entries = resourceKeys.filter(resource => resourceHref(resource).startsWith(`${prefix}/`));
  return <section className="space-y-6">
    <PageHeading title={groupLabels[group]} trail={group === "enrichment" ? [{ label: "Jobs", href: groupPaths.jobs }] : []} />
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{entries.map(resource => <Link key={resource} href={resourceHref(resource)} prefetch={false} className="rounded-lg border bg-card p-5 transition-colors hover:bg-accent focus-visible:outline-2 focus-visible:outline-ring">
      <div className="flex items-center justify-between gap-3 font-medium">{resources[resource].label}<ArrowRight aria-hidden size={16} /></div>
      <p className="mt-2 text-sm text-muted-foreground">{resources[resource].description}</p>
    </Link>)}</div>
  </section>;
}
