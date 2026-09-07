import type { AdminIdentity } from "@/lib/api/generated/models";

type Actor = Record<string, unknown> | null | undefined;
type Viewer = Pick<AdminIdentity, "subject" | "issuer" | "organization_id" | "name" | "email">;

function profileLabel(actor: Actor): string | undefined {
  for (const field of ["name", "email"]) {
    const value = actor?.[field];
    if (typeof value === "string" && value.trim() && value.trim() !== actor?.subject) return value.trim();
  }
}

/** Display names are optional; the immutable identity remains in the audit data. */
export function actorLabel(actor: Actor, viewer?: Viewer): string {
  const name = profileLabel(actor);
  if (name) return name;
  if (!actor?.subject) return "—";
  // Older proposals saved only the OIDC identity. Resolve this viewer by the
  // entire identity, since a subject can be reused by another issuer or tenant.
  if (viewer && (["subject", "issuer", "organization_id"] as const).every(field =>
    typeof actor[field] === "string" && actor[field] !== "" && actor[field] === viewer[field])) {
    return profileLabel(viewer) || "You";
  }
  if (actor.issuer === "devfeed" && actor.organization_id === "system" && actor.subject === "ai-analysis") return "AI analysis";
  return "Administrator";
}
