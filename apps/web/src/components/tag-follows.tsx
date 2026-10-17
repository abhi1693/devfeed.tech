"use client";

import { createFollowContext } from "./follow-context";

export const { Provider: TagFollowsProvider, useFollows: useTagFollows } = createFollowContext(
  "tag",
  "tag_ids",
);
