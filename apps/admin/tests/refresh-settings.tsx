import type { ReactNode } from "react";
import { act, fireEvent, screen } from "@testing-library/react";
import { AdminSession } from "@/components/molecules/admin-session";
import { useSettings } from "@/lib/use-settings";

/** Simulate saving the account preference without putting controls in a page. */
function SavedPreferenceChanges() {
  const { settings, save } = useSettings();
  return (
    <>
      {([0, 5, 10, 60] as const).map((seconds) => (
        <button
          key={seconds}
          onClick={() => void save("defaults", { ...settings.defaults, refresh_seconds: seconds })}
        >
          Save refresh {seconds}
        </button>
      ))}
    </>
  );
}
export function RefreshSettings({ children }: { children: ReactNode }) {
  return (
    <AdminSession
      admin={{
        subject: "test",
        issuer: "fixture",
        organization_id: "test",
        roles: ["superuser"],
        expires_at: 4102444800,
        csrf_token: "fixture",
      }}
    >
      <SavedPreferenceChanges />
      {children}
    </AdminSession>
  );
}
export async function saveRefresh(seconds: number) {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: `Save refresh ${seconds}` }));
  });
}
