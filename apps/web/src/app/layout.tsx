import { DeferredGoogleAnalytics } from "@/components/deferred-google-analytics";
import { ArticleNavigationProvider } from "@/components/article-navigation";
import { SourceFollowsProvider } from "@/components/source-follow";
import type { Metadata } from "next";
import { JsonLd } from "@/components/json-ld";
import { siteStructuredData } from "@/lib/structured-data";
import { canonicalUrl, socialMetadata, SITE_DESCRIPTION } from "@/lib/metadata";
import { connection } from "next/server";
import { analyticsMeasurementId } from "@/lib/server/config";
import brandMark from "@devfeed/theme/assets/devfeed-mark.png";
import "./globals.css";
import { themeScript } from "@/lib/theme";
import { NotificationPreferencesProvider } from "@/components/notification-preferences-provider";
import { UserProvider } from "@/components/user-account";
import { ThemePreferencesProvider } from "@/components/theme-preferences";
import { FeedPreferencesProvider } from "@/components/feed-preferences";
export async function generateMetadata(): Promise<Metadata> {
  await connection();
  return {
    robots: { "max-image-preview": "large" },
    metadataBase: new URL(canonicalUrl("/")),
    ...socialMetadata("DevFeed — Developer news", SITE_DESCRIPTION),
    icons: {
      icon: { url: brandMark.src, type: "image/png" },
      apple: brandMark.src,
    },
    title: {
      default: "DevFeed — Developer news",
      template: "%s · DevFeed",
    },
    description: SITE_DESCRIPTION,
  };
}
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
        <JsonLd data={siteStructuredData(brandMark.src)} />
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
