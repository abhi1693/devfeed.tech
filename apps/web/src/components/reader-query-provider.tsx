"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";

/** Mounted inside UserProvider's account boundary; private data stays in this tab/session. */
export function ReaderQueryProvider({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: Infinity,
            gcTime: 0,
            retry: false,
            networkMode: "always",
            refetchOnWindowFocus: false,
            refetchOnReconnect: (query) => query.state.status === "error",
          },
          // Writes must fail visibly rather than being replayed on a different session.
          mutations: { retry: false, networkMode: "always" },
        },
      }),
  );
  useEffect(() => () => client.clear(), [client]);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
