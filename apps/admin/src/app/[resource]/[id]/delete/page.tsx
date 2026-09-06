import { notFound } from "next/navigation";
import { isResource, resources } from "@/lib/resources";
import { ResourceDelete } from "@/components/organisms/resource-delete";
export default async function DeletePage({ params }: { params: Promise<{ resource: string; id: string }> }) {
  const { resource, id } = await params; if (!isResource(resource) || resources[resource].readonly) notFound();
  return <ResourceDelete key={`${resource}/${id}`} resource={resource} id={id} />;
}
