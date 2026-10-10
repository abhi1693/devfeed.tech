"use client";
import { useEffect } from "react";
import { usePathname } from "next/navigation";
import type { Faro } from "@grafana/faro-web-sdk";
import { routeName, type BrowserSettings } from "./privacy";
import { initializeBrowserTelemetry } from "./browser-initialize";

let initialized = false;
let instance: Faro | undefined;
export function BrowserTelemetry(settings: BrowserSettings) {
  const pathname = usePathname();
  useEffect(() => {
    if (!pathname || !instance) return;
    instance.api.setView({ name: routeName(pathname) });
    instance.api.pushEvent("route_change", { url: pathname });
  }, [pathname]);
  useEffect(() => {
    if (!settings.enabled || initialized || navigator.doNotTrack === "1") return;
    initialized = true;
    void initializeBrowserTelemetry(settings)
      .then((faro) => {
        instance = faro;
      })
      .catch(() => {
        initialized = false;
      });
  }, [settings.enabled, settings.app, settings.version, settings.environment]);
  return null;
}
