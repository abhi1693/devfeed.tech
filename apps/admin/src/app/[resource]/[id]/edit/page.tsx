import { notFound } from "next/navigation";
import { isResource, resources } from "@/lib/resources";
import { ResourceForm } from "@/components/organisms/resource-form";
export default async function EditPage({ params }: { params: Promise<{ resource: string; id: string }> }) {
  const { resource, id } = await params; if (!isResource(resource) || resources[resource].readonly) notFound();
  return <ResourceForm key={`${resource}/${id}`} resource={resource} id={id} />;
}
