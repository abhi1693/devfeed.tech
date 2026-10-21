"use client";

import type { ReactNode } from "react";
import { useFeedPreferences } from "./feed-preferences";
import { ColumnsWorkspace } from "./columns-workspace";
import { useUser } from "./user-account";

/** Keep workspace configuration separate from each route's normal article feed. */
export function ReaderFeedLayout({ children }: { children: ReactNode }) {
  const { view } = useFeedPreferences();
  const { user } = useUser();
  return view === "columns" ? <ColumnsWorkspace key={user?.user_id ?? "guest"} /> : children;
}
