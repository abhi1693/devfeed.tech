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

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <Toaster />
        {children}
      </body>
    </html>
  );
}
