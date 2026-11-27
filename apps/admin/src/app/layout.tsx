import { connection } from "next/server";
import type { Metadata } from "next";
import browserIcon from "@devfeed/theme/assets/devfeed-icon-32.png";
import appleIcon from "@devfeed/theme/assets/devfeed-icon-180.png";
import { Toaster } from "@/components/atoms/sonner";
import { isHardwareDaily, productName } from "@devfeed/theme/product";
import hardwareIcon from "@devfeed/theme/assets/hardware-daily-icon-32.png";
import hardwareAppleIcon from "@devfeed/theme/assets/hardware-daily-icon-180.png";
import "./globals.css";

export async function generateMetadata(): Promise<Metadata> {
  await connection();
  const adminSiteTitle = productName() + " Admin";
  return {
    icons: {
      icon: {
        url: (isHardwareDaily() ? hardwareIcon : browserIcon).src,
        type: "image/png",
        sizes: "32x32",
      },
      apple: {
        url: (isHardwareDaily() ? hardwareAppleIcon : appleIcon).src,
        type: "image/png",
        sizes: "180x180",
      },
    },
    title: { default: adminSiteTitle, template: `%s · ${adminSiteTitle}` },
    description: productName() + " site administration",
    robots: { index: false, follow: false },
  };
}

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  await connection();
  return (
    <html lang="en" data-product={isHardwareDaily() ? "hardwaredaily" : "devfeed"}>
      <body>
        <Toaster />
        {children}
      </body>
    </html>
  );
}
