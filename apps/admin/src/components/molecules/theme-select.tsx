"use client";

import { useState } from "react";
import { Monitor, Moon, Sun } from "lucide-react";
import { Select } from "./select";
import { useSettings } from "@/lib/use-settings";

export function ThemeSelect() {
  const { settings, save } = useSettings();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const Icon = { system: Monitor, light: Sun, dark: Moon }[settings.appearance.theme];
  return <div className="relative w-32">
    <Select label="Theme" required value={settings.appearance.theme} disabled={busy} icon={<Icon size={16} aria-hidden />}
      options={[{ value: "system", label: "System" }, { value: "light", label: "Light" }, { value: "dark", label: "Dark" }]}
      onChange={async theme => {
        setBusy(true); setError(false);
        try { await save("appearance", { ...settings.appearance, theme: theme as "system" | "light" | "dark" }); }
        catch { setError(true); }
        finally { setBusy(false); }
      }} />
    {error && <span role="alert" className="text-xs text-destructive">Couldn’t save theme. Try again.</span>}
  </div>;
}
