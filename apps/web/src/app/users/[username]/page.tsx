import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { publicDevCard, publicReadingActivity } from "@/lib/server/public-dev-card";
import { publicSiteOrigin } from "@/lib/server/config";
import { PublicUserProfile } from "@/components/public-user-profile";
import { UserShell } from "@/components/user-shell";

export const dynamic = "force-dynamic";
type Props = { params: Promise<{ username: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { username } = await params;
  const profile = await publicDevCard(username);
  if (!profile) return { title: "Profile unavailable", robots: { index: false, follow: false } };
  const title = `${profile.display_name || username}’s profile`;
  const description =
    profile.bio ||
    `Explore ${profile.display_name || username}’s stack and reading journey on DevFeed.`;
  const url = `${publicSiteOrigin()}/users/${username}`;
  const images = [{ url: `${url}/image`, width: 1200, height: 630, alt: title }];
  return {
    title,
    description,
    alternates: { canonical: url },
    robots: { index: false, follow: true },
    openGraph: { title, description, url, type: "website", images },
    twitter: { card: "summary_large_image", title, description, images },
  };
}

export default async function Page({ params }: Props) {
  const profile = await publicDevCard((await params).username);
  if (!profile) notFound();
  return (
    <UserShell>
      <PublicUserProfile
        profile={profile}
        activity={await publicReadingActivity(profile.username!)}
      />
    </UserShell>
  );
}
