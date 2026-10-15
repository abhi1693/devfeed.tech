"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

type SponsorshipContext = {
  target: HTMLDivElement | null;
  register: (target: HTMLDivElement | null) => void;
  available: boolean | null;
};
const Context = createContext<SponsorshipContext | null>(null);

export function HomepageSponsorshipProvider({ children }: { children: ReactNode }) {
  const [target, register] = useState<HTMLDivElement | null>(null);
  const [available, setAvailable] = useState<boolean | null>(null);
  useEffect(() => {
    const media = window.matchMedia("(min-width: 1280px)");
    const update = () => setAvailable(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return <Context.Provider value={{ target, register, available }}>{children}</Context.Provider>;
}

export function HomepageSponsorshipSlot() {
  const context = useContext(Context);
  const register = context?.register;
  return context ? <div ref={register} className="topbar-sponsorship" /> : null;
}

export function useHomepageSponsorship() {
  const context = useContext(Context);
  return { ready: context?.available !== null, available: context?.available === true };
}

export function HomepageSponsorship({ children }: { children: ReactNode }) {
  const context = useContext(Context);
  return context?.target && context.available ? createPortal(children, context.target) : null;
}
