"use client";

import { useEffect, useRef } from "react";
import { ArrowUpRight, Globe, Puzzle, X } from "lucide-react";
import { extensionStores } from "@/lib/extension-install";
import styles from "./extension-install-prompt.module.css";

const seenKey = "devfeed:extension-install-seen";
let shownThisSession = false;

export function ExtensionInstallPrompt() {
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    if (shownThisSession) return;
    try {
      if (localStorage.getItem(seenKey)) return;
    } catch {
      // The invitation still works when browser storage is unavailable.
    }
    const element = dialog.current;
    if (!element) return;
    element.showModal();
    shownThisSession = true;
    try {
      localStorage.setItem(seenKey, "1");
    } catch {
      // Remember it in memory for the rest of this visit instead.
    }
  }, []);

  const dismiss = () => dialog.current?.close();

  return (
    <dialog
      ref={dialog}
      className={styles.dialog}
      aria-labelledby="extension-install-title"
      aria-describedby="extension-install-description"
      onClick={(event) => {
        if (event.target === event.currentTarget) dismiss();
      }}
    >
      <div className={styles.content}>
        <button
          className={styles.close}
          aria-label="Close installation invitation"
          onClick={dismiss}
        >
          <X size={20} />
        </button>
        <div className={styles.icon}>
          <Puzzle size={28} aria-hidden="true" />
        </div>
        <p className={styles.eyebrow}>DEVFEED FOR YOUR BROWSER</p>
        <h2 id="extension-install-title">A fresh feed in every new tab.</h2>
        <p id="extension-install-description" className={styles.description}>
          Keep developer news, your favorite sources, and your personal feed one new tab away. Add
          DevFeed to your browser.
        </p>
        <div className={styles.stores}>
          <a
            href={extensionStores.chrome}
            target="_blank"
            rel="noopener noreferrer"
            onClick={dismiss}
          >
            <Globe size={24} aria-hidden="true" />
            <span>
              Install for Chrome<small>Chrome Web Store</small>
            </span>
            <ArrowUpRight size={18} aria-hidden="true" />
          </a>
          <a
            href={extensionStores.edge}
            target="_blank"
            rel="noopener noreferrer"
            onClick={dismiss}
          >
            <Puzzle size={24} aria-hidden="true" />
            <span>
              Install for Edge<small>Microsoft Edge Add-ons</small>
            </span>
            <ArrowUpRight size={18} aria-hidden="true" />
          </a>
        </div>
        <button className={styles.skip} onClick={dismiss}>
          Continue in browser
        </button>
      </div>
    </dialog>
  );
}
