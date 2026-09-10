import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { WorkerDetails } from "@/components/organisms/workers";

export const metadata: Metadata = { title: "Worker details" };
export default async function WorkerPage({ params }: { params: Promise<{ name: string }> }) {
  const { name } = await params;
  if (!/^[a-zA-Z0-9_.:-]{1,256}$/.test(name) || name === "." || name === "..") notFound();
  return <WorkerDetails key={name} name={name} />;
}
