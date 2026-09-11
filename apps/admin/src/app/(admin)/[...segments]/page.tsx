import { notFound, permanentRedirect } from "next/navigation";
import type { Metadata } from "next";
import { adminRouteTitle } from "@/lib/page-titles";
import { canonicalAdminRedirect, resolveAdminRoute, type RouteSearch } from "@/lib/routes";
import { ResourceGroup } from "@/components/organisms/resource-group";
import { ResourceList } from "@/components/organisms/resource-list";
import { ResourceDetail } from "@/components/organisms/resource-detail";
import { ResourceForm } from "@/components/organisms/resource-form";
import { ResourceDelete } from "@/components/organisms/resource-delete";
import { ResourceWorkflow } from "@/components/organisms/resource-workflow";
import { TopicImport } from "@/components/organisms/topic-import";
import { TopicProposalReview } from "@/components/organisms/topic-proposals";
import { RelationshipDiscovery } from "@/components/organisms/relationship-discovery";
import { RelationshipProposals, RelationshipProposalReview } from "@/components/organisms/relationship-proposals";
import { TopicEnrichment } from "@/components/organisms/topic-enrichment";

export async function generateMetadata({ params }: { params: Promise<{ segments: string[] }> }): Promise<Metadata> {
  const route = resolveAdminRoute((await params).segments);
  return { title: route ? adminRouteTitle(route) : "Page not found" };
}

export default async function AdminRoutePage({ params, searchParams }: {
  params: Promise<{ segments: string[] }>; searchParams: Promise<RouteSearch>;
}) {
  const { segments } = await params;
  const redirect = canonicalAdminRedirect(segments, await searchParams);
  if (redirect) permanentRedirect(redirect);
  const route = resolveAdminRoute(segments);
  if (!route) notFound();
  const key = segments.join("/");
  switch (route.view) {
    case "group": return <ResourceGroup group={route.group} />;
    case "list": return <ResourceList key={key} resource={route.resource} analysisType={route.analysisType} />;
    case "new": return <ResourceForm key={key} resource={route.resource} />;
    case "edit": return <ResourceForm key={key} resource={route.resource} id={route.id} />;
    case "delete": return <ResourceDelete key={key} resource={route.resource} id={route.id} />;
    case "detail": return <ResourceDetail key={key} resource={route.resource} id={route.id} section={route.section} />;
    case "workflow": return <ResourceWorkflow key={key} resource={route.resource} id={route.id} action={route.action} />;
    case "import": return <TopicImport />;
    case "proposal": return <TopicProposalReview key={key} id={route.id} />;
    case "relationship-discover": return <RelationshipDiscovery key={key} />;
    case "relationship-proposals": return <RelationshipProposals />;
    case "relationship-proposal": return <RelationshipProposalReview key={key} id={route.id} />;
    case "enrich": return <TopicEnrichment key={key} id={route.id} />;
  }
}
