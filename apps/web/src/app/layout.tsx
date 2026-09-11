import { ArticleNavigationProvider } from "@/components/article-navigation";
import { SourceFollowsProvider } from "@/components/source-follow";
import type { Metadata } from "next";
import brandMark from "@devfeed/theme/assets/devfeed-mark.png";
import "./globals.css";
import { themeScript } from "@/lib/theme";
import { NotificationPreferencesProvider } from "@/components/notification-preferences-provider";
import { UserProvider } from "@/components/user-account";
import { ThemePreferencesProvider } from "@/components/theme-preferences";
import { FeedPreferencesProvider } from "@/components/feed-preferences";
export const metadata: Metadata = {
  icons: {
    icon: { url: brandMark.src, type: "image/png" },
    apple: brandMark.src,
  },
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
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>
        <UserProvider>
          <ThemePreferencesProvider>
          <NotificationPreferencesProvider>
            <FeedPreferencesProvider>
            <ArticleNavigationProvider>
            <SourceFollowsProvider>
            {children}
            {modal}
            </SourceFollowsProvider>
            </ArticleNavigationProvider>
            </FeedPreferencesProvider>
          </NotificationPreferencesProvider>
          </ThemePreferencesProvider>
        </UserProvider>
      </body>
    </html>
  );
}
