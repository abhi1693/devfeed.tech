import { notFound } from "next/navigation";
import { isResource } from "@/lib/resources";
import { ResourceList } from "@/components/organisms/resource-list";
export default async function ListPage({ params }: { params: Promise<{ resource: string }> }) {
  const { resource } = await params; if (!isResource(resource)) notFound();
  return <ResourceList key={resource} resource={resource} />;
}
