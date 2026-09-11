"use client";

import { ThemeToggleButton } from "@devfeed/ui/theme-toggle";
import { setBrowserTheme, useBrowserTheme } from "@/lib/browser-theme";

export function ThemeToggle() {
  const theme = useBrowserTheme();
  return <ThemeToggleButton theme={theme} onChange={setBrowserTheme} />;
}
