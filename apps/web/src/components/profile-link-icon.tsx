import { Globe, Link2 } from "lucide-react";
import {
  siGithub,
  siGitlab,
  siBitbucket,
  siCodeberg,
  siSourcehut,
  siStackoverflow,
  siStackexchange,
  siDevdotto,
  siHashnode,
  siMedium,
  siSubstack,
  siX,
  siBluesky,
  siMastodon,
  siThreads,
  siReddit,
  siYcombinator,
  siLobsters,
  siYoutube,
  siTwitch,
  siDiscord,
  siTelegram,
  siInstagram,
  siCodesandbox,
  siStackblitz,
  siReplit,
  siGlitch,
  siObservable,
  siNpm,
  siPypi,
  siRust,
  siDocker,
  siHuggingface,
  siKaggle,
  siLeetcode,
  siHackerrank,
  siCodewars,
  siExercism,
  siCodeforces,
  siTryhackme,
  siHackthebox,
  siFreecodecamp,
  siProducthunt,
  siIndiehackers,
  siWellfound,
  siDribbble,
  siBehance,
  siFigma,
  siBuymeacoffee,
  siKofi,
  siPatreon,
  siOpencollective,
  siKeybase,
  siWakatime,
  siLinktree,
} from "simple-icons";
import { profileLinkLabel } from "@/lib/profile-links";

// Bundled SVGs only: typing a profile URL never makes a favicon/network request.
const icons: Record<string, string> = {
  GitHub: siGithub.path,
  "GitHub Pages": siGithub.path,
  GitLab: siGitlab.path,
  Bitbucket: siBitbucket.path,
  Codeberg: siCodeberg.path,
  SourceHut: siSourcehut.path,
  "Stack Overflow": siStackoverflow.path,
  "Stack Exchange": siStackexchange.path,
  "DEV Community": siDevdotto.path,
  Hashnode: siHashnode.path,
  Medium: siMedium.path,
  Substack: siSubstack.path,
  X: siX.path,
  Bluesky: siBluesky.path,
  Mastodon: siMastodon.path,
  Threads: siThreads.path,
  Reddit: siReddit.path,
  "Hacker News": siYcombinator.path,
  Lobsters: siLobsters.path,
  YouTube: siYoutube.path,
  Twitch: siTwitch.path,
  Discord: siDiscord.path,
  Telegram: siTelegram.path,
  Instagram: siInstagram.path,
  CodeSandbox: siCodesandbox.path,
  StackBlitz: siStackblitz.path,
  Replit: siReplit.path,
  Glitch: siGlitch.path,
  Observable: siObservable.path,
  npm: siNpm.path,
  PyPI: siPypi.path,
  "crates.io": siRust.path,
  "Docker Hub": siDocker.path,
  "Hugging Face": siHuggingface.path,
  Kaggle: siKaggle.path,
  LeetCode: siLeetcode.path,
  HackerRank: siHackerrank.path,
  Codewars: siCodewars.path,
  Exercism: siExercism.path,
  Codeforces: siCodeforces.path,
  TryHackMe: siTryhackme.path,
  "Hack The Box": siHackthebox.path,
  freeCodeCamp: siFreecodecamp.path,
  "Product Hunt": siProducthunt.path,
  "Indie Hackers": siIndiehackers.path,
  Wellfound: siWellfound.path,
  Dribbble: siDribbble.path,
  Behance: siBehance.path,
  Figma: siFigma.path,
  "Buy Me a Coffee": siBuymeacoffee.path,
  "Ko-fi": siKofi.path,
  Patreon: siPatreon.path,
  "Open Collective": siOpencollective.path,
  Keybase: siKeybase.path,
  WakaTime: siWakatime.path,
  Linktree: siLinktree.path,
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
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
          <path d={path} />
        </svg>
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
