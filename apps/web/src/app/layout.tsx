import { DeferredGoogleAnalytics } from "@/components/deferred-google-analytics";
import { ArticleNavigationProvider } from "@/components/article-navigation";
import { SourceFollowsProvider } from "@/components/source-follow";
import type { Metadata } from "next";
import { connection } from "next/server";
import { analyticsMeasurementId } from "@/lib/server/config";
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
  description: "Developer news, tutorials, and articles organized by topic and source.",
};
export default async function RootLayout({
  children,
  modal,
}: {
  children: React.ReactNode;
  modal?: React.ReactNode;
}) {
  // Read deployment settings per request, including on otherwise prerenderable pages.
  await connection();
  const gaId = analyticsMeasurementId();
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <link rel="alternate" type="text/plain" href="/llms.txt" title="AI discovery index" />
        <link
          rel="alternate"
          type="text/plain"
          href="/llms-full.txt"
          title="AI reading guide and public content"
        />
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
        {gaId && <DeferredGoogleAnalytics gaId={gaId} />}
      </body>
    </html>
  );
}
