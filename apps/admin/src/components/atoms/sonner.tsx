"use client";

import { useSyncExternalStore, type CSSProperties } from "react";
import { CircleCheckIcon, CircleXIcon, InfoIcon, LoaderCircleIcon, TriangleAlertIcon } from "lucide-react";
import { Toaster as Sonner } from "sonner";

const darkMode = () => document.documentElement.classList.contains("dark");
const subscribeTheme = (callback: () => void) => { const observer = new MutationObserver(callback); observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] }); return () => observer.disconnect(); };

/** One application-wide host; notifications survive client-side navigation. */
export function Toaster() {
  const dark = useSyncExternalStore(subscribeTheme, darkMode, () => false);
  return <Sonner theme={dark ? "dark" : "light"} position="bottom-right" richColors closeButton
    duration={5000} visibleToasts={3} offset={24} mobileOffset={16}
    containerAriaLabel="Notifications" className="toaster"
    icons={{
      success: <CircleCheckIcon className="size-4" />,
      error: <CircleXIcon className="size-4" />,
      warning: <TriangleAlertIcon className="size-4" />,
      info: <InfoIcon className="size-4" />,
      loading: <LoaderCircleIcon className="size-4 animate-spin" />,
    }}
    style={{
      "--normal-bg": "var(--popover)",
      "--normal-text": "var(--popover-foreground)",
      "--normal-border": "var(--border)",
      "--border-radius": "var(--radius)",
      fontFamily: "inherit",
    } as CSSProperties}
    toastOptions={{ classNames: { toast: "break-words", description: "whitespace-pre-line" } }} />;
}
