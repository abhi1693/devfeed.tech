import { getFeed, UserApiError } from "../api";
import { parseFilters, type FeedFilters, type SearchParams } from "../feed-query";
import { feedMetadata } from "../metadata";

/** Index only collection views that actually contain articles for this reader. */
export async function collectionFeedMetadata(
  title: string,
  description: string,
  query: SearchParams,
  scope: Partial<FeedFilters>,
) {
  const metadata = feedMetadata(title, description, query, scope);
  try {
    const feed = await getFeed(parseFilters({ ...query, ...scope }));
    if (feed.items.length) return metadata;
  } catch (error) {
    // FeedView renders a retry state for service failures; do not index that state.
    if (!(error instanceof UserApiError)) throw error;
  }
  return { ...metadata, robots: { index: false, follow: true } };
}
