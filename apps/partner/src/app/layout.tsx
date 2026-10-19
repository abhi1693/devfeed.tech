import type { Metadata } from "next";
import browserIcon from "@devfeed/theme/assets/devfeed-icon-32.png";
import appleIcon from "@devfeed/theme/assets/devfeed-icon-180.png";
import { themeScript } from "@devfeed/ui/theme-script";
import "./globals.css";

export const metadata: Metadata = {
  icons: {
    icon: { url: browserIcon.src, type: "image/png", sizes: "32x32" },
    apple: { url: appleIcon.src, type: "image/png", sizes: "180x180" },
  },
  title: "DevFeed Partners",
  description: "Your partnership and product performance on DevFeed.",
  robots: { index: false, follow: false },
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
