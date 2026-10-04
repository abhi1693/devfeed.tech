import { TopicFollowsProvider } from "@/components/topic-follows";
import { BrowserTelemetry } from "@devfeed/telemetry/browser";
import { browserSettings } from "@devfeed/telemetry/receiver";
import { DeferredGoogleAnalytics } from "@/components/deferred-google-analytics";
import { DeferredClarity } from "@/components/deferred-clarity";
import { ArticleNavigationProvider } from "@/components/article-navigation";
import { SourceFollowsProvider } from "@/components/source-follow";
import type { Metadata } from "next";
import Script from "next/script";
import { JsonLd } from "@/components/json-ld";
import { siteStructuredData } from "@/lib/structured-data";
import { canonicalUrl, socialMetadata, SITE_DESCRIPTION } from "@/lib/metadata";
import { connection } from "next/server";
import { headers } from "next/headers";
import { analyticsMeasurementId, clarityProjectId, xPixelEnabled } from "@/lib/server/config";
import brandMark from "@devfeed/theme/assets/devfeed-mark.png";
import browserIcon from "@devfeed/theme/assets/devfeed-icon-32.png";
import appleIcon from "@devfeed/theme/assets/devfeed-icon-180.png";
import "./globals.css";
import "./reader-motion.css";
import "./article-share.css";
import { themeScript } from "@/lib/theme";
import { NotificationPreferencesProvider } from "@/components/notification-preferences-provider";
import { UserProvider } from "@/components/user-account";
import { ThemePreferencesProvider } from "@/components/theme-preferences";
import { FeedPreferencesProvider } from "@/components/feed-preferences";
import { WebSignupNudge } from "@/components/web-signup-nudge";
import { ReaderNavigationRecovery } from "@/components/reader-navigation-recovery";
export async function generateMetadata(): Promise<Metadata> {
  await connection();
  return {
    robots: { "max-image-preview": "large" },
    metadataBase: new URL(canonicalUrl("/")),
    ...socialMetadata("DevFeed — Developer news", SITE_DESCRIPTION),
    icons: {
      icon: { url: browserIcon.src, type: "image/png", sizes: "32x32" },
      apple: { url: appleIcon.src, type: "image/png", sizes: "180x180" },
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
  const nonce = (await headers()).get("content-security-policy")?.match(/'nonce-([^']+)'/)?.[1];
  const gaId = analyticsMeasurementId();
  const clarityId = clarityProjectId();
  const enableXPixel = xPixelEnabled();
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
        <script nonce={nonce} dangerouslySetInnerHTML={{ __html: themeScript }} />
        {enableXPixel && (
          <Script id="x-pixel" strategy="afterInteractive" nonce={nonce}>
            {`!function(e,t,n,s,u,a){e.twq||(s=e.twq=function(){s.exe?s.exe.apply(s,arguments):s.queue.push(arguments);
},s.version='1.1',s.queue=[],u=t.createElement(n),u.async=!0,u.src='https://static.ads-twitter.com/uwt.js',
a=t.getElementsByTagName(n)[0],a.parentNode.insertBefore(u,a))}(window,document,'script');
twq('config','pc5f8');`}
          </Script>
        )}
      </head>
      <body>
        <ReaderNavigationRecovery />
        <BrowserTelemetry {...browserSettings("web")} />
        <JsonLd data={siteStructuredData(brandMark.src)} />
        <UserProvider>
          <ThemePreferencesProvider>
            <NotificationPreferencesProvider>
              <FeedPreferencesProvider>
                <ArticleNavigationProvider>
                  <SourceFollowsProvider>
                    <TopicFollowsProvider>
                      {children}
                      {modal}
                    </TopicFollowsProvider>
                  </SourceFollowsProvider>
                  <WebSignupNudge />
                </ArticleNavigationProvider>
              </FeedPreferencesProvider>
            </NotificationPreferencesProvider>
          </ThemePreferencesProvider>
        </UserProvider>
        {gaId && <DeferredGoogleAnalytics gaId={gaId} />}
        {clarityId && <DeferredClarity projectId={clarityId} />}
      </body>
    </html>
  );
}
