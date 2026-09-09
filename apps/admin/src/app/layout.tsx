import type { Metadata } from "next";
import { Toaster } from "@/components/atoms/sonner";
import { adminSiteTitle } from "@/lib/page-titles";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: adminSiteTitle, template: `%s · ${adminSiteTitle}` },
  description: "DevFeed site administration",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><Toaster />{children}</body></html>;
}
