import { connection } from "next/server";
import { BrowserTelemetry } from "@devfeed/telemetry/browser";
import { browserSettings } from "@devfeed/telemetry/receiver";
import type { Metadata } from "next";
import browserIcon from "@devfeed/theme/assets/devfeed-icon-32.png";
import appleIcon from "@devfeed/theme/assets/devfeed-icon-180.png";
import { Toaster } from "@/components/atoms/sonner";
import { adminSiteTitle } from "@/lib/page-titles";
import "./globals.css";

export const metadata: Metadata = {
  icons: {
    icon: { url: browserIcon.src, type: "image/png", sizes: "32x32" },
    apple: { url: appleIcon.src, type: "image/png", sizes: "180x180" },
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
