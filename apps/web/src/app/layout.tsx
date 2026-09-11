import type { Metadata } from "next";
import "./globals.css";
import { UserProvider } from "@/components/user-account";
export const metadata: Metadata = {
  title: {
    default: "DevFeed — Developer news",
    template: "%s · DevFeed",
  },
  description:
    "Developer news, tutorials, and articles organized by topic and source.",
};
export default function RootLayout({
  children,
  modal,
}: {
  children: React.ReactNode;
  modal?: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <UserProvider>
          {children}
          {modal}
        </UserProvider>
      </body>
    </html>
  );
}
