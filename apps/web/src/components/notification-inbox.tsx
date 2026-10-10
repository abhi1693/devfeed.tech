"use client";

import { lazy, Suspense } from "react";
import { useUser } from "./user-account";

// Anonymous readers never download the inbox UI, client or event-stream code.
const UserInbox = lazy(() =>
  import("./user-inbox").then((module) => ({ default: module.UserInbox })),
);

export function NotificationInbox() {
  const { user, loading } = useUser();
  return user && !loading ? (
    <Suspense fallback={null}>
      <UserInbox key={user.user_id} user={user} />
    </Suspense>
  ) : null;
}
