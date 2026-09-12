import { Skeleton } from "@/components/atoms/skeleton";

export default function Loading() {
  return (
    <main
      role="status"
      aria-label="Loading administration"
      className="w-full min-w-0 space-y-6 px-4 py-6 sm:px-6"
    >
      <Skeleton className="h-7 w-40" />
      <div className="grid gap-4 sm:grid-cols-3">
        {[0, 1, 2].map((item) => (
          <Skeleton key={item} className="h-24" />
        ))}
      </div>
    </main>
  );
}
