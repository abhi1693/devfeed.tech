import { connection } from "next/server";
import { BrowserTelemetry } from "@devfeed/telemetry/browser";
import { browserSettings } from "@devfeed/telemetry/receiver";
import type { Metadata } from "next";
import brandMark from "@devfeed/theme/assets/devfeed-mark.png";
import { Toaster } from "@/components/atoms/sonner";
import { adminSiteTitle } from "@/lib/page-titles";
import "./globals.css";

export const metadata: Metadata = {
  icons: {
    icon: { url: brandMark.src, type: "image/png" },
    apple: brandMark.src,
  },
  title: { default: adminSiteTitle, template: `%s · ${adminSiteTitle}` },
  description: "DevFeed site administration",
  robots: { index: false, follow: false },
};

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  await connection();
  return (
    <html lang="en">
      <body>
        <BrowserTelemetry {...browserSettings("admin")} />
        <Toaster />
        {children}
      </body>
    </html>
  );
}
