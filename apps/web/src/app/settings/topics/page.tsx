import Link from "next/link";
import type { Metadata } from "next";
import { UserShell } from "@/components/user-shell";
import { TopicPreferences } from "@/components/topic-preferences";
import { getTopics } from "@/lib/api";
import type { Topic } from "@/lib/types";
export const dynamic = "force-dynamic";
export const metadata: Metadata = {
  title: "Your topics",
  robots: { index: false, follow: false },
};
export default async function TopicsSettings() {
  const topics: Topic[] = [];
  try {
    // Catalog paging is bounded by the catalog itself, not a fixed first-page cutoff.
    for (let offset = 0; ; offset += 500) {
      const batch = await getTopics(offset, 500);
      topics.push(...batch);
      if (batch.length < 500) break;
    }
  } catch {
    return (
      <UserShell section="account">
        <section className="empty-state">
          <h1>Couldn’t load topics</h1>
          <p>Please try again shortly.</p>
          <Link className="button" href="/settings/topics">
            Try again
          </Link>
        </section>
      </UserShell>
    );
  }
  return (
    <UserShell section="account">
      <TopicPreferences topics={topics} />
    </UserShell>
  );
}
