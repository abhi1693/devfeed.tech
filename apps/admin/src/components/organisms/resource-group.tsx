import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { ResourceHeading } from "@devfeed/ui/resource-layout";
import { resourceGroups, resourceKeys, resources, type RouteGroup } from "@/lib/resources";
import { resourceHref } from "@/lib/routes";

export function ResourceGroup({ group }: { group: RouteGroup }) {
  const definition = resourceGroups[group];
  const entries = resourceKeys.filter((resource) =>
    resources[resource].navigation.groups.includes(group),
  );
  return (
    <section className="space-y-6">
      <ResourceHeading
        title={definition.label}
        trail={definition.breadcrumbs.map(({ label, href }) => ({ label, href }))}
      />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {entries.map((resource) => (
          <Link
            key={resource}
            href={resourceHref(resource)}
            prefetch={false}
            className="rounded-lg border bg-card p-5 transition-colors hover:bg-accent focus-visible:outline-2 focus-visible:outline-ring"
          >
            <div className="flex items-center justify-between gap-3 font-medium">
              {resources[resource].label}
              <ArrowRight aria-hidden size={16} />
            </div>
            <p className="mt-2 text-sm text-muted-foreground">{resources[resource].description}</p>
          </Link>
        ))}
      </div>
    </section>
  );
}
