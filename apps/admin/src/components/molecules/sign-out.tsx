"use client";

import { useState } from "react";
import { Button } from "@/components/atoms/button";
import { adminAuthLogout } from "@/lib/api/generated/admin";
import { returnToLogin } from "@/lib/api/client";
import { notifyFailure } from "@/lib/notifications";

export function SignOut({ csrfToken }: { csrfToken: string }) {
  const [busy, setBusy] = useState(false);
  async function signOut() {
    if (busy) return;
    setBusy(true);
    try {
      await adminAuthLogout({ headers: { "X-CSRF-Token": csrfToken } });
      returnToLogin(true);
    } catch (error) {
      notifyFailure(error, "Could not sign out");
      setBusy(false);
    }
  }
  return <div className="flex flex-col items-end gap-1">
    <Button variant="outline" size="sm" onClick={signOut} loading={busy} loadingText="Signing out…">
      Sign out
    </Button>
  </div>;
}
