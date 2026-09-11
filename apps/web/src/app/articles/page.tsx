import ContentFeed, { generateMetadata as contentMetadata } from "../[contentType]/page";
import type { SearchParams } from "@/lib/feed-query";

export const dynamic = "force-dynamic";
type Props = { searchParams: Promise<SearchParams> };
export function generateMetadata({ searchParams }: Props) {
  return contentMetadata({ params: Promise.resolve({ contentType: "articles" }), searchParams });
}
export default function Articles({ searchParams }: Props) {
  return ContentFeed({ params: Promise.resolve({ contentType: "articles" }), searchParams });
}
