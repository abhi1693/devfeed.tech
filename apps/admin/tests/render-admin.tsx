import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { AdminSession } from "@/components/molecules/admin-session";

export function renderAdmin(children: ReactNode) {
  return render(<AdminSession admin={{ subject: "admin", issuer: "https://identity.example", organization_id: "org", roles: ["superuser"], expires_at: 4102444800, csrf_token: "test-csrf" }}>{children}</AdminSession>);
}
