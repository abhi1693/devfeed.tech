"use client";

import { Moon, Sun } from "lucide-react";

export function ThemeToggleButton({ onClick, disabled = false }: { onClick: () => void; disabled?: boolean }) {
  return (
    <button className="theme-toggle" type="button" onClick={onClick} disabled={disabled}>
      <span className="theme-to-dark" title="Switch to dark theme">
        <Moon size={19} aria-hidden="true" />
        <span className="sr-only">Switch to dark theme</span>
      </span>
      <span className="theme-to-light" title="Switch to light theme">
        <Sun size={19} aria-hidden="true" />
        <span className="sr-only">Switch to light theme</span>
      </span>
    </button>
  );
}
