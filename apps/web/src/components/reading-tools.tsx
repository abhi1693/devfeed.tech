"use client";

import { lazy, Suspense } from "react";
import { useUser } from "./user-account";

const AccountReadingTools = lazy(() =>
  import("./account-reading-tools").then((module) => ({ default: module.AccountReadingTools })),
);

export function ReadingTools() {
  const { user } = useUser();
  return user ? (
    <Suspense fallback={null}>
      <AccountReadingTools key={user.user_id} />
    </Suspense>
  ) : null;
}
