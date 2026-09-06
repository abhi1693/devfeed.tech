import type { Metadata } from "next";
import { Toaster } from "@/components/atoms/sonner";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "DevFeed Admin", template: "%s · DevFeed Admin" },
  description: "DevFeed site administration",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><Toaster />{children}</body></html>;
}
