"use client";

import { useState } from "react";
import { ThemeToggleButton } from "@devfeed/ui/theme-toggle";
import { useSettings } from "@/lib/use-settings";
import { notifyFailure } from "@/lib/notifications";

export function ThemeToggle() {
  const { settings, save } = useSettings();
  const [busy, setBusy] = useState(false);
  async function toggle() {
    if (busy) return;
    const theme = document.documentElement.classList.contains("dark") ? "light" : "dark";
    setBusy(true);
    try {
      await save("appearance", { ...settings.appearance, theme });
    } catch (error) {
      notifyFailure(error, "Couldn’t save theme");
    } finally {
      setBusy(false);
    }
  }
  return <ThemeToggleButton onClick={toggle} disabled={busy} />;
}
