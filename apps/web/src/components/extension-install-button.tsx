"use client";

import Image from "next/image";
import { useSyncExternalStore } from "react";
import { extensionBrowser, extensionStores } from "@/lib/extension-install";

const subscribe = () => () => {};
const browser = () => extensionBrowser(navigator.userAgent, window.location.protocol);
const serverBrowser = () => null;

export function ExtensionInstallButton() {
  const target = useSyncExternalStore(subscribe, browser, serverBrowser);
  if (!target) return null;
  return (
    <a
      className="button extension-install-button"
      href={extensionStores[target]}
      target="_blank"
      rel="noopener noreferrer"
    >
      <Image src={`/browser-icons/${target}.svg`} width={14} height={14} alt="" unoptimized />
      Get for {target === "edge" ? "Edge" : "Chrome"}
    </a>
  );
}
