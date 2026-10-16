"use client";

import { Component, lazy, Suspense, useSyncExternalStore, type ReactNode } from "react";

const PromoContent = lazy(() => import("./dev-card-promo-content"));
const subscribe = () => () => {};

class PromoBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}

export function DevCardPromo({ requested = false }: { requested?: boolean }) {
  const mounted = useSyncExternalStore(
    subscribe,
    () => true,
    () => false,
  );
  // An optional invitation must not make its artwork and dialog CSS block the reader's first paint.
  // Suspense waits for the dialog's stylesheet before mounting its focus and timing effects.
  return mounted ? (
    <PromoBoundary>
      <Suspense fallback={null}>
        <PromoContent requested={requested} />
      </Suspense>
    </PromoBoundary>
  ) : null;
}
