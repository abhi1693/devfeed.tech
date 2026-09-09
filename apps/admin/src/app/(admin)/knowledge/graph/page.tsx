import type { Metadata } from "next";
import { KnowledgeGraph } from "@/components/organisms/knowledge-graph";

export const metadata: Metadata = { title: "Knowledge graph" };
export default function KnowledgeGraphPage() { return <KnowledgeGraph />; }
