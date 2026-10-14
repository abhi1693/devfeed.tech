"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  FileText,
  Rss,
  Shapes,
  Tags,
  GitBranch,
  Users,
  Cable,
  Building2,
  Package,
  Workflow,
  ClipboardCheck,
  Cpu,
  ListOrdered,
  ChartNoAxesCombined,
  Download,
  FileSearch,
  Image,
  Globe,
  BrainCircuit,
  BellRing,
} from "lucide-react";
import { CollapsibleSidebar } from "@devfeed/ui/sidebar";
import { resourceHref } from "@/lib/routes";
import { resourceKeys, resources, type Resource } from "@/lib/resources";
const icons: Record<Resource, typeof FileText> = {
  users: Users,
  articles: FileText,
  sources: Rss,
  topics: Shapes,
  tags: Tags,
  "topic-relations": GitBranch,
  "ingestion-jobs": Download,
  "article-jobs": FileSearch,
  "image-jobs": Image,
  "source-jobs": Globe,
  "analysis-jobs": BrainCircuit,
  "notification-jobs": BellRing,
};
export function Sidebar() {
  const pathname = usePathname();
  const linkClass = (active: boolean) => `nav-item${active ? " active" : ""}`;
  return (
    <CollapsibleSidebar
      id="admin-sidebar"
      label="Administration navigation"
      storageKey="devfeed:admin-sidebar-expanded"
      variant="workspace"
    >
      <nav aria-label="Administration">
        <Link
          href="/"
          prefetch={false}
          aria-current={pathname === "/" ? "page" : undefined}
          className={linkClass(pathname === "/")}
          title="Overview"
          aria-label="Overview"
        >
          <LayoutDashboard size={20} aria-hidden />
          <span>Overview</span>
        </Link>
        {["Content", "Taxonomy", "Audience", "Partnerships", "Operations"].map((group) => (
          <section key={group} className="sidebar-group" aria-label={group}>
            <h2 className="sidebar-section-title">{group}</h2>
            <ul className="space-y-0.5">
              {group === "Partnerships" &&
                [
                  { label: "Partner accounts", href: "/partnerships/accounts", icon: Building2 },
                  {
                    label: "Import connections",
                    href: "/partnerships/connections",
                    icon: Cable,
                  },
                  { label: "Products", href: "/partnerships/products", icon: Package },
                  { label: "Pipeline jobs", href: "/partnerships/pipeline", icon: Workflow },
                  { label: "Evaluations", href: "/partnerships/evaluations", icon: ClipboardCheck },
                ].map((item) => (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      prefetch={false}
                      className={linkClass(
                        pathname === item.href || pathname.startsWith(`${item.href}/`),
                      )}
                      aria-current={
                        pathname === item.href || pathname.startsWith(`${item.href}/`)
                          ? "page"
                          : undefined
                      }
                      title={item.label}
                      aria-label={item.label}
                    >
                      <item.icon size={20} aria-hidden />
                      <span>{item.label}</span>
                    </Link>
                  </li>
                ))}
              {group === "Operations" &&
                [
                  { label: "Workers", href: "/workers", icon: Cpu },
                  { label: "Queues", href: "/queues", icon: ListOrdered },
                  {
                    label: "Search analytics",
                    href: "/search-analytics",
                    icon: ChartNoAxesCombined,
                  },
                ].map((item) => (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      prefetch={false}
                      aria-current={
                        pathname === item.href || pathname.startsWith(`${item.href}/`)
                          ? "page"
                          : undefined
                      }
                      className={linkClass(
                        pathname === item.href || pathname.startsWith(`${item.href}/`),
                      )}
                      title={item.label}
                      aria-label={item.label}
                    >
                      <item.icon size={20} aria-hidden />
                      <span>{item.label}</span>
                    </Link>
                  </li>
                ))}
              {resourceKeys
                .filter((key) => resources[key].sidebarGroup === group)
                .map((key) => {
                  const Icon = icons[key];
                  const active =
                    pathname === resourceHref(key) || pathname.startsWith(`${resourceHref(key)}/`);
                  return (
                    <li key={key}>
                      <Link
                        href={resourceHref(key)}
                        prefetch={false}
                        aria-current={active ? "page" : undefined}
                        title={resources[key].label}
                        aria-label={resources[key].label}
                        className={linkClass(active)}
                      >
                        <Icon size={20} aria-hidden />
                        <span>{resources[key].label}</span>
                      </Link>
                    </li>
                  );
                })}
            </ul>
          </section>
        ))}
      </nav>
    </CollapsibleSidebar>
  );
}
