import { notFound } from "next/navigation";
import { isResource, resources } from "@/lib/resources";
import { ResourceForm } from "@/components/organisms/resource-form";
export default async function NewPage({ params }: { params: Promise<{ resource: string }> }) {
  const { resource } = await params; if (!isResource(resource) || resources[resource].readonly) notFound();
  return <ResourceForm key={resource} resource={resource} />;
}
