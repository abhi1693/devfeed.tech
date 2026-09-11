import { UserShell } from "@/components/user-shell";
import { LoadingSkeleton } from "@/components/loading-skeleton";
export default function Loading() {
  return <UserShell section="account"><LoadingSkeleton kind="form" label="Loading settings…" /></UserShell>;
}
