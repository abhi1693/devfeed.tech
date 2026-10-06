"use client";

import { useEffect, useState } from "react";
import { BookOpen, Flame, Medal, Trophy } from "lucide-react";
import Link from "@/components/reader-link";
import { readerLoginLink, readerRequest } from "@/lib/reader-runtime";
import { userRequest } from "@/lib/user";
import type { LeaderboardEntry, MyReadingRanks, ReadingLeaderboard } from "@/lib/leaderboard";
import { useUser } from "./user-account";
import { ProfileAvatar } from "./profile-avatar";
import styles from "./leaderboard.module.css";

const boards = [
  {
    key: "longest_streak",
    title: "Longest streak",
    Icon: Flame,
  },
  {
    key: "reading_days",
    title: "Most reading days",
    Icon: BookOpen,
  },
] as const;

export function Leaderboard() {
  const { user, loading, profile, profileUnavailable, sessionRevision } = useUser();
  const [attempt, setAttempt] = useState(0);
  const [standings, setStandings] = useState<{
    attempt: number;
    value?: ReadingLeaderboard;
    error?: boolean;
  }>();
  const [personal, setPersonal] = useState<{
    key: string;
    value?: MyReadingRanks;
    error?: boolean;
  }>();
  const userId = user?.user_id;
  const personalKey = `${userId}:${sessionRevision}:${attempt}`;
  const own = personal?.key === personalKey ? personal : undefined;
  const current = standings?.attempt === attempt ? standings : undefined;

  useEffect(() => {
    const controller = new AbortController();
    void readerRequest("/api/v1/leaderboard", {
      credentials: "omit",
      cache: "no-store",
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("Leaderboard unavailable");
        const value = (await response.json()) as ReadingLeaderboard;
        if (!controller.signal.aborted) setStandings({ attempt, value });
      })
      .catch(() => {
        if (!controller.signal.aborted) setStandings({ attempt, error: true });
      });
    return () => controller.abort();
  }, [attempt]);

  useEffect(() => {
    if (!userId) return;
    const controller = new AbortController();
    void userRequest<MyReadingRanks>("leaderboard/me", {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]),
    })
      .then((value) => {
        if (!controller.signal.aborted) setPersonal({ key: personalKey, value });
      })
      .catch(() => {
        if (!controller.signal.aborted) setPersonal({ key: personalKey, error: true });
      });
    return () => controller.abort();
  }, [userId, personalKey]);

  const retry = () => setAttempt((value) => value + 1);
  return (
    <div className={styles.page}>
      <header className="page-heading">
        <h1>
          <Trophy className={styles.titleIcon} size={25} aria-hidden="true" />
          Leaderboard
        </h1>
      </header>
      {current?.error ? (
        <section className={styles.notice} role="alert">
          <h2>Leaderboard unavailable</h2>
          <button className="button" onClick={retry}>
            Try again
          </button>
        </section>
      ) : !current?.value ? (
        <p className={styles.notice} role="status">
          Loading the leaderboard…
        </p>
      ) : (
        <div className={styles.boards}>
          {boards.map(({ key, title, Icon }) => {
            const entries = current.value![key];
            const mine = own?.value?.[key];
            const ownRankListed = entries.some((entry) => entry.username === mine?.username);
            return (
              <section key={key} className={styles.board} aria-label={title} data-metric={key}>
                <header>
                  <h2>
                    <Icon size={21} aria-hidden="true" />
                    {title}
                  </h2>
                </header>
                {entries.length ? (
                  <ol className={styles.list} aria-label={`${title} rankings`}>
                    {entries.map((entry) => (
                      <li key={entry.username}>
                        <RankingRow entry={entry} own={mine?.username === entry.username} />
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className={styles.empty}>No rankings yet.</p>
                )}
                {!ownRankListed && (
                  <footer
                    className={styles.personal}
                    aria-label={`Your ${title.toLowerCase()} ranking`}
                  >
                    {loading ? (
                      <p role="status">Checking your account…</p>
                    ) : !user ? (
                      <a className={styles.action} {...readerLoginLink("/leaderboard")}>
                        Sign in to join
                      </a>
                    ) : own?.error ? (
                      <button className={styles.action} onClick={retry}>
                        Retry your ranking
                      </button>
                    ) : !own?.value ? (
                      <p role="status">Loading your ranking…</p>
                    ) : mine ? (
                      <RankingRow entry={mine} own />
                    ) : profile?.username && profile.visibility?.public ? (
                      <Link className={styles.action} href="/latest">
                        Start reading
                      </Link>
                    ) : (
                      <Link className={styles.action} href="/settings/profile">
                        {profileUnavailable
                          ? "Review your profile"
                          : profile?.username
                            ? "Make your profile public"
                            : "Claim your username"}
                      </Link>
                    )}
                  </footer>
                )}
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}

function RankingRow({ entry, own = false }: { entry: LeaderboardEntry; own?: boolean }) {
  const name = entry.display_name || entry.username;
  return (
    <Link
      className={styles.row}
      href={`/users/${encodeURIComponent(entry.username)}`}
      data-own={own}
    >
      <span className={styles.rank} data-podium={entry.rank <= 3 ? entry.rank : undefined}>
        {entry.rank <= 3 ? <Medal size={20} aria-hidden="true" /> : null}
        <span>#{entry.rank.toLocaleString("en-US")}</span>
      </span>
      <ProfileAvatar name={name} url={entry.avatar_url} />
      <span className={styles.identity}>
        <strong>
          {name}
          {own && <small>You</small>}
        </strong>
        <span>@{entry.username}</span>
      </span>
      <span className={styles.score}>
        {entry.days.toLocaleString("en-US")}
        <small>{entry.days === 1 ? "day" : "days"}</small>
      </span>
    </Link>
  );
}
