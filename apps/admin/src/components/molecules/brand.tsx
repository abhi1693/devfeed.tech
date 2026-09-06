import Link from "next/link";

export function Brand() {
  return <Link href="/" className="inline-flex items-baseline gap-2 rounded-sm focus-visible:outline-2">
    <span className="text-lg font-semibold tracking-tight">DevFeed</span>
    <span className="text-sm text-muted-foreground">Admin</span>
  </Link>;
}
