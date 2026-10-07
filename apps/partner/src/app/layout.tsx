import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DevFeed Partners",
  description: "Your partnership and product performance on DevFeed.",
  robots: { index: false, follow: false },
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
