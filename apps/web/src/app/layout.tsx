import { TagFollowsProvider } from "@/components/tag-follows";
import { HeaderSponsorshipProvider } from "@/components/header-sponsorship";
import { TopicFollowsProvider } from "@/components/topic-follows";
import { DeferredGoogleAnalytics } from "@/components/deferred-google-analytics";
import { DeferredClarity } from "@/components/deferred-clarity";
import { WebArticleNavigationProvider } from "@/components/web-article-navigation";
import { SourceFollowsProvider } from "@/components/source-follow";
import type { Metadata } from "next";
import { JsonLd } from "@/components/json-ld";
import { siteStructuredData } from "@/lib/structured-data";
import { canonicalUrl, socialMetadata, SITE_DESCRIPTION } from "@/lib/metadata";
import { connection } from "next/server";
import { headers } from "next/headers";
import { analyticsMeasurementId, clarityProjectId } from "@/lib/server/config";
import brandMark from "@devfeed/theme/assets/devfeed-mark.png";
import browserIcon from "@devfeed/theme/assets/devfeed-icon-32.png";
import appleIcon from "@devfeed/theme/assets/devfeed-icon-180.png";
import { ReaderBrandProvider } from "@/components/reader-brand";
import { isHardwareDaily, readerName, HARDWARE_DESCRIPTION } from "@/lib/reader-brand";
import "./globals.css";
import "./reader-motion.css";
import "./article-actions.css";
import { themeScript } from "@devfeed/ui/theme-script";
import { UserProvider } from "@/components/user-account";
import { ThemePreferencesProvider } from "@/components/theme-preferences";
import { FeedPreferencesProvider } from "@/components/feed-preferences";
import { SignupNudge } from "@/components/signup-nudge";
import { ReaderContextProvider } from "@/components/reader-context";
import { ReaderPromptsProvider } from "@/components/reader-prompts";
import { ReaderNavigationRecovery } from "@/components/reader-navigation-recovery";
export async function generateMetadata(): Promise<Metadata> {
  await connection();
  const hardware = isHardwareDaily();
  const title = hardware
    ? "Hardware Daily — Electronics and embedded engineering"
    : "DevFeed — Developer news";
  const description = hardware ? HARDWARE_DESCRIPTION : SITE_DESCRIPTION;
  return {
    robots: { "max-image-preview": "large" },
    metadataBase: new URL(canonicalUrl("/")),
    ...socialMetadata(title, description),
    icons: {
      icon: {
        url: hardware ? "/hardware-daily/icon-32.png" : browserIcon.src,
        type: "image/png",
        sizes: "32x32",
      },
      apple: {
        url: hardware ? "/hardware-daily/icon-180.png" : appleIcon.src,
        type: "image/png",
        sizes: "180x180",
      },
    },
    title: {
      default: title,
      template: `%s · ${readerName()}`,
    },
    description,
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
      </head>
      <body>
        <ReaderNavigationRecovery />
        <JsonLd
          data={siteStructuredData(
            isHardwareDaily()
              ? { src: "/hardware-daily/icon-512.png", width: 512, height: 512 }
              : brandMark,
          )}
        />
        <ReaderBrandProvider value={isHardwareDaily()}>
          <UserProvider>
            <ReaderContextProvider>
              <ReaderPromptsProvider>
                <HeaderSponsorshipProvider>
                  <ThemePreferencesProvider>
                    <FeedPreferencesProvider>
                      <WebArticleNavigationProvider>
                        <SourceFollowsProvider>
                          <TagFollowsProvider>
                            <TopicFollowsProvider>
                              {children}
                              {modal}
                            </TopicFollowsProvider>
                          </TagFollowsProvider>
                        </SourceFollowsProvider>
                        <SignupNudge />
                      </WebArticleNavigationProvider>
                    </FeedPreferencesProvider>
                  </ThemePreferencesProvider>
                </HeaderSponsorshipProvider>
              </ReaderPromptsProvider>
            </ReaderContextProvider>
          </UserProvider>
        </ReaderBrandProvider>
        {gaId && <DeferredGoogleAnalytics gaId={gaId} />}
        {clarityId && <DeferredClarity projectId={clarityId} />}
      </body>
    </html>
  );
}
