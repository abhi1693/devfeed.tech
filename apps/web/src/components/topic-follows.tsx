"use client";

import { createFollowContext } from "./follow-context";

export const { Provider: TopicFollowsProvider, useFollows: useTopicFollows } = createFollowContext(
  "topic",
  "topic_ids",
);
