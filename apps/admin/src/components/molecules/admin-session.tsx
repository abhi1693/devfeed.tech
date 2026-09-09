"use client";
import { createContext, useContext } from "react";
import type { AdminIdentity, UserSettings } from "@/lib/api/generated/models";
import { SettingsProvider } from "@/lib/use-settings";
const Context = createContext<AdminIdentity | null>(null);
export function AdminSession({ admin, settings, children }: { admin: AdminIdentity; settings?: UserSettings; children: React.ReactNode }) { return <Context.Provider value={admin}><SettingsProvider key={JSON.stringify([admin.issuer, admin.organization_id, admin.subject])} admin={admin} initial={settings}>{children}</SettingsProvider></Context.Provider>; }
export function useAdmin() { const value = useContext(Context); if (!value) throw new Error("Admin session required"); return value; }
