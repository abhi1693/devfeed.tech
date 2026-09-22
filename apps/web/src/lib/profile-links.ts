// Match parsed hostnames, never URL text: paths and lookalike domains are not providers.
// Unrecognized sites (including custom domains) use the generic Website label.
const sites: Record<string, string> = {
  "github.com": "GitHub",
  "github.io": "GitHub Pages",
  "gitlab.com": "GitLab",
  "bitbucket.org": "Bitbucket",
  "codeberg.org": "Codeberg",
  "sr.ht": "SourceHut",
  "stackoverflow.com": "Stack Overflow",
  "stackexchange.com": "Stack Exchange",
  "dev.to": "DEV Community",
  "hashnode.com": "Hashnode",
  "hashnode.dev": "Hashnode",
  "medium.com": "Medium",
  "substack.com": "Substack",
  "linkedin.com": "LinkedIn",
  "x.com": "X",
  "twitter.com": "X",
  "bsky.app": "Bluesky",
  "mastodon.social": "Mastodon",
  "hachyderm.io": "Mastodon",
  "fosstodon.org": "Mastodon",
  "threads.net": "Threads",
  "threads.com": "Threads",
  "reddit.com": "Reddit",
  "news.ycombinator.com": "Hacker News",
  "lobste.rs": "Lobsters",
  "youtube.com": "YouTube",
  "youtu.be": "YouTube",
  "twitch.tv": "Twitch",
  "discord.com": "Discord",
  "discord.gg": "Discord",
  "t.me": "Telegram",
  "instagram.com": "Instagram",
  "codepen.io": "CodePen",
  "codesandbox.io": "CodeSandbox",
  "stackblitz.com": "StackBlitz",
  "replit.com": "Replit",
  "glitch.com": "Glitch",
  "observablehq.com": "Observable",
  "npmjs.com": "npm",
  "pypi.org": "PyPI",
  "crates.io": "crates.io",
  "hub.docker.com": "Docker Hub",
  "huggingface.co": "Hugging Face",
  "kaggle.com": "Kaggle",
  "leetcode.com": "LeetCode",
  "hackerrank.com": "HackerRank",
  "codewars.com": "Codewars",
  "exercism.org": "Exercism",
  "codeforces.com": "Codeforces",
  "atcoder.jp": "AtCoder",
  "tryhackme.com": "TryHackMe",
  "hackthebox.com": "Hack The Box",
  "freecodecamp.org": "freeCodeCamp",
  "producthunt.com": "Product Hunt",
  "indiehackers.com": "Indie Hackers",
  "wellfound.com": "Wellfound",
  "dribbble.com": "Dribbble",
  "behance.net": "Behance",
  "figma.com": "Figma",
  "npm.io": "npm.io",
  "buymeacoffee.com": "Buy Me a Coffee",
  "ko-fi.com": "Ko-fi",
  "patreon.com": "Patreon",
  "opencollective.com": "Open Collective",
  "keybase.io": "Keybase",
  "wakatime.com": "WakaTime",
  "linktr.ee": "Linktree",
};

/** A display name only, not a URL safety check or a verified account badge. No network requests. */
export function profileLinkLabel(value: string): string | null {
  try {
    const url = new URL(value.trim());
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    if (url.username || url.password) return null;
    const hostname = url.hostname
      .toLowerCase()
      .replace(/\.$/, "")
      .replace(/^www\./, "");
    // Most-specific domain first, with a dot boundary for subdomains.
    let domain = hostname;
    while (domain.includes(".")) {
      if (Object.hasOwn(sites, domain)) return sites[domain];
      domain = domain.slice(domain.indexOf(".") + 1);
    }
    return hostname ? "Website" : null;
  } catch {
    return null;
  }
}

export function profileLinkFromUrl(value: string) {
  const url = value.trim();
  return { url, label: profileLinkLabel(url) };
}
