"use client";
import { createContext, useContext } from "react";
import type { AdminIdentity } from "@/lib/api/generated/models";
const Context = createContext<AdminIdentity | null>(null);
export function AdminSession({ admin, children }: { admin: AdminIdentity; children: React.ReactNode }) { return <Context.Provider value={admin}>{children}</Context.Provider>; }
export function useAdmin() { const value = useContext(Context); if (!value) throw new Error("Admin session required"); return value; }
