"use client";

import {
  createContext,
  useContext,
  useCallback,
  useRef,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { PartnerPlacements } from "./partner-placements";

const SlotContext = createContext<((target: HTMLDivElement | null) => void) | null>(null);

/** Own one delivery for the reader session, independently of page and feed lifetimes. */
export function HeaderSponsorshipProvider({ children }: { children: ReactNode }) {
  const container = useRef<HTMLDivElement | null>(null);
  const [host, setHost] = useState<HTMLDivElement | null>(null);
  const [wide, setWide] = useState(false);
  const register = useCallback((target: HTMLDivElement | null) => {
    if (!target) {
      container.current?.remove();
      return;
    }
    const next = container.current ?? document.createElement("div");
    next.className = "topbar-sponsorship-content";
    container.current = next;
    target.appendChild(next);
    setHost(next);
  }, []);
  useEffect(() => {
    const media = window.matchMedia("(min-width: 1280px)");
    const update = () => setWide(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return (
    <SlotContext.Provider value={register}>
      {children}
      <PartnerPlacements
        surface="header"
        ad_formats={["header_sponsorship"]}
        maxItems={wide && host ? 1 : 0}
      >
        {(cards) => (wide && host ? createPortal(cards, host) : null)}
      </PartnerPlacements>
    </SlotContext.Provider>
  );
}

export function HeaderSponsorshipSlot() {
  const register = useContext(SlotContext);
  return register ? <div ref={register} className="topbar-sponsorship" /> : null;
}
