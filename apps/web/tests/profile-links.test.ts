import { expect, it } from "vitest";
import { profileLinkFromUrl, profileLinkLabel } from "@/lib/profile-links";

it.each([
  ["https://github.com/reader", "GitHub"],
  [" https://WWW.GITHUB.COM/reader ", "GitHub"],
  ["https://gist.github.com/reader", "GitHub"],
  ["https://github.com./reader", "GitHub"],
  ["https://reader.github.io", "GitHub Pages"],
  ["https://gitlab.com/reader", "GitLab"],
  ["https://bitbucket.org/reader", "Bitbucket"],
  ["https://codeberg.org/reader", "Codeberg"],
  ["https://git.sr.ht/~reader", "SourceHut"],
  ["https://stackoverflow.com/users/123/reader", "Stack Overflow"],
  ["https://unix.stackexchange.com/users/123/reader", "Stack Exchange"],
  ["https://dev.to/reader", "DEV Community"],
  ["https://reader.hashnode.dev", "Hashnode"],
  ["https://reader.medium.com", "Medium"],
  ["https://reader.substack.com", "Substack"],
  ["https://www.linkedin.com/in/reader", "LinkedIn"],
  ["https://twitter.com/reader", "X"],
  ["https://x.com/reader", "X"],
  ["https://bsky.app/profile/reader.dev", "Bluesky"],
  ["https://fosstodon.org/@reader", "Mastodon"],
  ["https://news.ycombinator.com/user?id=reader", "Hacker News"],
  ["https://www.youtube.com/@reader", "YouTube"],
  ["https://youtu.be/123", "YouTube"],
  ["https://discord.gg/abc", "Discord"],
  ["https://codepen.io/reader", "CodePen"],
  ["https://codesandbox.io/u/reader", "CodeSandbox"],
  ["https://stackblitz.com/@reader", "StackBlitz"],
  ["https://replit.com/@reader", "Replit"],
  ["https://www.npmjs.com/~reader", "npm"],
  ["https://pypi.org/user/reader", "PyPI"],
  ["https://crates.io/users/reader", "crates.io"],
  ["https://hub.docker.com/u/reader", "Docker Hub"],
  ["https://huggingface.co/reader", "Hugging Face"],
  ["https://kaggle.com/reader", "Kaggle"],
  ["https://leetcode.com/u/reader", "LeetCode"],
  ["https://www.hackerrank.com/reader", "HackerRank"],
  ["https://www.producthunt.com/@reader", "Product Hunt"],
  ["https://www.buymeacoffee.com/reader", "Buy Me a Coffee"],
  ["https://www.reader.dev/work?from=github.com", "Website"],
  ["https://gitlab.reader.dev/reader", "Website"],
  ["http://reader.example/about", "Website"],
  ["https://github.com.evil.example/reader", "Website"],
  ["https://notgithub.com/reader", "Website"],
  ["https://example.org/github.com", "Website"],
])("detects the site for %s", (url, label) => {
  expect(profileLinkLabel(url)).toBe(label);
});

it.each([
  "",
  "not a URL",
  "https://",
  "github.com/reader",
  "javascript:alert(1)",
  "data:text/html,github.com",
  "mailto:reader@github.com",
  "ftp://github.com/reader",
  "https://github.com@evil.example/reader",
])("does not label an incomplete or unsupported URL: %s", (url) => {
  expect(profileLinkLabel(url)).toBeNull();
});

it("trims URLs and labels custom domains as Website regardless of length", () => {
  expect(profileLinkFromUrl(" https://github.com/reader ")).toEqual({
    url: "https://github.com/reader",
    label: "GitHub",
  });
  const domain = `${"a".repeat(50)}.${"b".repeat(50)}.dev`;
  expect(profileLinkLabel(`https://${domain}`)).toBe("Website");
  expect(profileLinkFromUrl(`https://${domain}`)).toEqual({
    url: `https://${domain}`,
    label: "Website",
  });
});
