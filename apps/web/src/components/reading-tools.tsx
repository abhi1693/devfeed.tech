"use client";

import { Component, lazy, Suspense, type ReactNode } from "react";
import { useUser } from "./user-account";

const AccountReadingTools = lazy(() =>
  import("./account-reading-tools").then((module) => ({ default: module.AccountReadingTools })),
);

class ReadingToolsBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}

export function ReadingTools() {
  const { user } = useUser();
  return user ? (
    <ReadingToolsBoundary key={user.user_id}>
      <Suspense fallback={null}>
        <AccountReadingTools />
      </Suspense>
    </ReadingToolsBoundary>
  ) : null;
}
