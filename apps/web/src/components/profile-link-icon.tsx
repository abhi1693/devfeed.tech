import { Globe, Link2 } from "lucide-react";
import { profileLinkLabel } from "@/lib/profile-links";

// Only displayed brands are fetched from local assets; profile URLs never trigger remote requests.
const icons: Record<string, string> = {
  GitHub: "/profile-icons/github.e327163d1e9b.svg",
  "GitHub Pages": "/profile-icons/github.e327163d1e9b.svg",
  GitLab: "/profile-icons/gitlab.7739dfcae6ee.svg",
  Bitbucket: "/profile-icons/bitbucket.c6e64a3c4458.svg",
  Codeberg: "/profile-icons/codeberg.2473c458783d.svg",
  SourceHut: "/profile-icons/sourcehut.0e82e714f2e0.svg",
  "Stack Overflow": "/profile-icons/stackoverflow.1834e1326fe2.svg",
  "Stack Exchange": "/profile-icons/stackexchange.f45b932f8a39.svg",
  "DEV Community": "/profile-icons/devdotto.269267c29f8c.svg",
  Hashnode: "/profile-icons/hashnode.11319b9f49dd.svg",
  Medium: "/profile-icons/medium.74d19a8e64ae.svg",
  Substack: "/profile-icons/substack.396b5b14e619.svg",
  X: "/profile-icons/x.17390ecd514c.svg",
  Bluesky: "/profile-icons/bluesky.86255822a2bf.svg",
  Mastodon: "/profile-icons/mastodon.782769f4c54d.svg",
  Threads: "/profile-icons/threads.6f4bcf04216e.svg",
  Reddit: "/profile-icons/reddit.bc4d2ceb1470.svg",
  "Hacker News": "/profile-icons/ycombinator.e2f04913e44f.svg",
  Lobsters: "/profile-icons/lobsters.e13c3af2be72.svg",
  YouTube: "/profile-icons/youtube.a2193974a5ce.svg",
  Twitch: "/profile-icons/twitch.994bc281bab5.svg",
  Discord: "/profile-icons/discord.561c92c31d88.svg",
  Telegram: "/profile-icons/telegram.5e9864f6564a.svg",
  Instagram: "/profile-icons/instagram.934228422c5c.svg",
  CodeSandbox: "/profile-icons/codesandbox.00a28cbb9eb1.svg",
  StackBlitz: "/profile-icons/stackblitz.05fcb2bf1115.svg",
  Replit: "/profile-icons/replit.b642dd7feae1.svg",
  Glitch: "/profile-icons/glitch.95ff43c28c74.svg",
  Observable: "/profile-icons/observable.cf904b43149e.svg",
  npm: "/profile-icons/npm.675d600e4e7a.svg",
  PyPI: "/profile-icons/pypi.80a05372111d.svg",
  "crates.io": "/profile-icons/rust.aaba667c1126.svg",
  "Docker Hub": "/profile-icons/docker.25e23d51b216.svg",
  "Hugging Face": "/profile-icons/huggingface.fae3226076a0.svg",
  Kaggle: "/profile-icons/kaggle.682549670136.svg",
  LeetCode: "/profile-icons/leetcode.f63d62290e26.svg",
  HackerRank: "/profile-icons/hackerrank.b0b6a2afb31b.svg",
  Codewars: "/profile-icons/codewars.ca8c2c3e180c.svg",
  Exercism: "/profile-icons/exercism.73cf4256fd62.svg",
  Codeforces: "/profile-icons/codeforces.28989de7934c.svg",
  TryHackMe: "/profile-icons/tryhackme.e337746d2ff8.svg",
  "Hack The Box": "/profile-icons/hackthebox.fb57aca53597.svg",
  freeCodeCamp: "/profile-icons/freecodecamp.127e08f1ae69.svg",
  "Product Hunt": "/profile-icons/producthunt.6e22ad3df063.svg",
  "Indie Hackers": "/profile-icons/indiehackers.8e74c7790cdc.svg",
  Wellfound: "/profile-icons/wellfound.6607a921cbb5.svg",
  Dribbble: "/profile-icons/dribbble.2414641943ae.svg",
  Behance: "/profile-icons/behance.384496c5a036.svg",
  Figma: "/profile-icons/figma.70cc881276c4.svg",
  "Buy Me a Coffee": "/profile-icons/buymeacoffee.cfa3575ee384.svg",
  "Ko-fi": "/profile-icons/kofi.a15f33e026d7.svg",
  Patreon: "/profile-icons/patreon.5b0ea6857a23.svg",
  "Open Collective": "/profile-icons/opencollective.3009bd820171.svg",
  Keybase: "/profile-icons/keybase.548b8dc51aef.svg",
  WakaTime: "/profile-icons/wakatime.446fb3989a50.svg",
  Linktree: "/profile-icons/linktree.d21664ada5e6.svg",
};

export function ProfileLinkIcon({ url }: { url: string }) {
  const site = profileLinkLabel(url);
  const path = site ? icons[site] : undefined;
  return (
    <span
      className="profile-link-icon"
      role="img"
      aria-label={site ?? "Link"}
      title={site ?? "Link"}
    >
      {path ? (
        <span
          aria-hidden="true"
          className="profile-brand-mark"
          style={{
            display: "inline-block",
            width: 16,
            height: 16,
            backgroundColor: "currentColor",
            forcedColorAdjust: "none",
            maskImage: `url("${path}")`,
            WebkitMaskImage: `url("${path}")`,
            maskSize: "contain",
            maskRepeat: "no-repeat",
          }}
        />
      ) : site === "LinkedIn" ? (
        // Same monochrome LinkedIn mark as the article share control.
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
          <circle cx="6.3" cy="6.5" r="1.5" />
          <path d="M5 9h2.6v10H5zm5 0h2.5v1.4C13.1 9.4 14.1 9 15.4 9c2.7 0 3.6 1.6 3.6 4.5V19h-2.7v-5c0-1.5-.3-2.5-1.7-2.5-1.5 0-1.9 1.1-1.9 2.5v5H10Z" />
        </svg>
      ) : site ? (
        <Globe size={16} aria-hidden="true" />
      ) : (
        <Link2 size={16} aria-hidden="true" />
      )}
    </span>
  );
}
