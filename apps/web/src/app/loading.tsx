import { UserShell } from "@/components/user-shell";
import { LoadingSkeleton } from "@/components/loading-skeleton";
export default function Loading() {
  return (
    <UserShell>
      <LoadingSkeleton />
    </UserShell>
  );
}
