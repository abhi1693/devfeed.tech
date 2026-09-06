import { notFound } from "next/navigation";
import { isResource } from "@/lib/resources";
import { ResourceDetail } from "@/components/organisms/resource-detail";
export default async function DetailPage({ params }: { params: Promise<{ resource: string; id: string }> }) {
  const { resource, id } = await params; if (!isResource(resource)) notFound();
  return <ResourceDetail key={`${resource}/${id}`} resource={resource} id={id} />;
}
