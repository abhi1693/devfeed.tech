"use client";

import { createContext, useContext } from "react";
import brandMark from "@devfeed/theme/assets/devfeed-mark.png";

import Image from "next/image";
import Link from "@/components/reader-link";

const HardwareDailyContext = createContext(false);

export function useReaderBrand() {
  return useContext(HardwareDailyContext)
    ? { name: "Hardware Daily", label: "Hardware Daily", mark: "/hardware-daily/mark.svg" }
    : { name: "DevFeed", label: "devfeed.", mark: brandMark };
}

export function ReaderBrandLink({ login = false }: { login?: boolean }) {
  const brand = useReaderBrand();
  const size = login ? 34 : 40;
  return (
    <Link
      href={login ? "/latest" : "/"}
      className={login ? undefined : "brand devfeed-brand"}
      aria-label={brand.name + " home"}
    >
      <Image
        className={login ? undefined : "devfeed-brand-mark"}
        src={brand.mark}
        alt=""
        width={size}
        height={size}
        priority
      />
      <span>{brand.label}</span>
    </Link>
  );
}

export function ReaderBrandProvider({
  value,
  children,
}: {
  value: boolean;
  children: React.ReactNode;
}) {
  return <HardwareDailyContext.Provider value={value}>{children}</HardwareDailyContext.Provider>;
}
