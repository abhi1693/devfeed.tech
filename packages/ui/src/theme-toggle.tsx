"use client";

import { Monitor, Moon, Sun } from "lucide-react";

export type ThemeMode = "light" | "dark" | "system";
const modes: Record<ThemeMode, { label: string; next: ThemeMode; icon: typeof Sun }> = {
  light: { label: "Light", next: "dark", icon: Sun },
  dark: { label: "Dark", next: "system", icon: Moon },
  system: { label: "System", next: "light", icon: Monitor },
};

export function ThemeToggleButton({ theme, onChange, disabled = false }: { theme: ThemeMode; onChange: (theme: ThemeMode) => void; disabled?: boolean }) {
  const { label, next, icon: Icon } = modes[theme];
  const description = `Theme: ${label}. Switch to ${next} theme`;
  return <button className="theme-toggle" type="button" onClick={() => onChange(next)} disabled={disabled} aria-label={description} title={description}>
    <Icon size={19} aria-hidden="true" />
  </button>;
}
