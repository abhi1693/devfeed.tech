import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { PageHeading } from "@/components/molecules/page-heading";
export const metadata = { title: "Partnerships" };
export default function Page() {
  return (
    <section className="space-y-6">
      <PageHeading title="Partnerships" />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {[
          {
            name: "Partners",
            href: "/partnerships/partners",
            description: "Manage supported partner connections and settings.",
          },
          {
            name: "Products",
            href: "/partnerships/products",
            description: "View synced products, platform listings, and automatic checks.",
          },
          {
            name: "Pipeline jobs",
            href: "/partnerships/pipeline",
            description: "Inspect discovery, product syncs, qualification, and worker logs.",
          },
          {
            name: "Evaluations",
            href: "/partnerships/evaluations",
            description: "Inspect article matching runs, decisions, and evidence.",
          },
        ].map((item) => (
          <Link
            key={item.href}
            href={item.href}
            prefetch={false}
            className="rounded-lg border bg-card p-5 transition-colors hover:bg-accent focus-visible:outline-2 focus-visible:outline-ring"
          >
            <div className="flex items-center justify-between gap-3 font-medium">
              {item.name}
              <ArrowRight aria-hidden size={16} />
            </div>
            <p className="mt-2 text-sm text-muted-foreground">{item.description}</p>
          </Link>
        ))}
      </div>
    </section>
  );
}
