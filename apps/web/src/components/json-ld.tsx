import type { StructuredData } from "@/lib/structured-data";

/** Publisher-controlled strings must never be able to close this script element. */
export function JsonLd({ data }: { data: StructuredData }) {
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(data).replace(/</g, "\\u003c") }}
    />
  );
}
