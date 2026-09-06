import { notFound } from "next/navigation";
import { ResourceWorkflow } from "@/components/organisms/resource-workflow";
export default async function WorkflowPage({ params }: { params: Promise<{ resource: string; id: string; action: string }> }) {
  const { resource, id, action } = await params;
  if (resource !== "articles" && resource !== "sources") notFound();
  if (action !== "review" && !(action === "classify" && resource === "articles") && !(action === "fetch" && resource === "sources")) notFound();
  return <ResourceWorkflow key={`${resource}/${id}/${action}`} resource={resource} id={id} action={action} />;
}
