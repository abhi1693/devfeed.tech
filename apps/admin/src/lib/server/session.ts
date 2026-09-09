import "server-only";
import { cache } from "react";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import type { AdminIdentity, AdminOverview, AuthConfig, UserSettings } from "@/lib/api/generated/models";
import { adminApiOrigin } from "./config";

export async function currentAdmin(): Promise<AdminIdentity | null> {
  const jar = await cookies();
  const session = jar.get("__Host-devfeed_admin_session") ?? jar.get("devfeed_admin_session");
  if (!session) return null;
  const response = await fetch(`${adminApiOrigin()}/v1/admin/auth/me`, {
    headers: { Cookie: `${session.name}=${session.value}` },
    cache: "no-store", redirect: "error", signal: AbortSignal.timeout(10_000),
  });
  if (response.status === 401 || response.status === 403) return null;
  if (!response.ok) throw new Error("Admin service unavailable");
  return response.json() as Promise<AdminIdentity>;
}

export async function requireAdmin() {
  const admin = await currentAdmin();
  if (!admin) redirect("/login");
  return admin;
}

export async function authConfiguration(): Promise<AuthConfig> {
  const response = await fetch(`${adminApiOrigin()}/v1/admin/auth/config`, {
    cache: "no-store", redirect: "error", signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error("Admin service unavailable");
  return response.json() as Promise<AuthConfig>;
}

export async function initialOverview(days = 30): Promise<AdminOverview> {
  const jar = await cookies();
  const session = jar.get("__Host-devfeed_admin_session") ?? jar.get("devfeed_admin_session");
  if (!session) redirect("/login");
  const response = await fetch(`${adminApiOrigin()}/v1/admin/overview?days=${days}`, {
    headers: { Cookie: `${session.name}=${session.value}` }, cache: "no-store",
    redirect: "error", signal: AbortSignal.timeout(10_000),
  });
  if (response.status === 401 || response.status === 403) redirect("/login");
  if (!response.ok) throw new Error("Admin service unavailable");
  return response.json() as Promise<AdminOverview>;
}

export const initialUserSettings = cache(async (): Promise<UserSettings> => {
  const jar = await cookies();
  const session = jar.get("__Host-devfeed_admin_session") ?? jar.get("devfeed_admin_session");
  if (!session) redirect("/login");
  const response = await fetch(`${adminApiOrigin()}/v1/admin/settings`, {
    headers: { Cookie: `${session.name}=${session.value}` }, cache: "no-store",
    redirect: "error", signal: AbortSignal.timeout(10_000),
  });
  if (response.status === 401 || response.status === 403) redirect("/login");
  if (!response.ok) throw new Error("Could not load account settings");
  return response.json() as Promise<UserSettings>;
});
