import { githubAvatarFixture } from "../../../../scripts/testing/optimized-avatars.mjs";

export const readerProfile = {
  username: "leader-reader",
  display_name: "Leading Reader",
  avatar_url: githubAvatarFixture,
  bio: "Reading every day.",
  links: [
    { url: "https://github.com/leader-reader", label: "GitHub" },
    { url: "https://gitlab.com/leader-reader", label: "GitLab" },
    { url: "https://x.com/leader-reader", label: "X" },
  ],
};
