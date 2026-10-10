"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Popover } from "radix-ui";
import { ArrowRight, Award, BookOpen, Check, RotateCcw, Trophy, X } from "lucide-react";
import { useUser } from "./user-account";
import { userRequest, type ReadingStreak as Streak } from "@/lib/user";
import {
  getStreakProgress,
  openMustReadsEvent,
  utcDay,
  validateReadingWeek,
  type ReadingWeek,
} from "@/lib/reading-streak";

export function ReadingStreak() {
  const { user, profile, sessionRevision, loading } = useUser();
  if (loading || !user || !profile?.reading_streak) return null;
  return (
    <StreakCard
      key={user.user_id + ":" + sessionRevision}
      owner={user.user_id}
      revision={sessionRevision}
      streak={profile.reading_streak}
    />
  );
}

export function ReadingStreakProgress() {
  const { user, profile, loading } = useUser();
  const today = useReadingDay();
  if (loading || !user || !profile?.reading_streak) return null;
  const state = getStreakProgress(profile.reading_streak, today);
  const dayUnit = state.current === 1 ? "day" : "days";
  return (
    <div className="user-menu-reading-streak">
      <div className="user-menu-reading-streak-heading">
        <span>Reading streak</span>
        <strong>
          {state.current} {dayUnit}
        </strong>
      </div>
      <span className="sr-only">
        {state.remaining} {state.remaining === 1 ? "day" : "days"} to the next milestone of{" "}
        {state.next} days.
      </span>
      <div className="reading-streak-progress" aria-hidden="true">
        <span style={{ width: state.progress + "%" }} />
      </div>
    </div>
  );
}

function useReadingDay() {
  const [today, setToday] = useState(utcDay);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const update = () => {
      clearTimeout(timer);
      setToday(utcDay());
      const now = new Date();
      const midnight = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1);
      timer = setTimeout(update, midnight - now.getTime() + 10);
    };
    update();
    window.addEventListener("focus", update);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("focus", update);
    };
  }, []);
  return today;
}

function StreakCard({
  owner,
  revision,
  streak,
}: {
  owner: string;
  revision: number;
  streak: Streak;
}) {
  const today = useReadingDay();
  const [open, setOpen] = useState(false);
  const [celebration, setCelebration] = useState<string>();
  const previous = useRef(streak);
  const openPicks = useRef(false);
  const picksScope = useRef<{ mounted: boolean; timer?: number }>({
    mounted: true,
  });
  useEffect(() => {
    const scope = picksScope.current;
    scope.mounted = true;
    return () => {
      scope.mounted = false;
      clearTimeout(scope.timer);
    };
  }, []);
  useEffect(() => {
    const mobile = window.matchMedia("(max-width: 800px)");
    const closeOnMobile = () => {
      if (mobile.matches) setOpen(false);
    };
    mobile.addEventListener("change", closeOnMobile);
    return () => mobile.removeEventListener("change", closeOnMobile);
  }, []);
  useEffect(() => {
    const before = previous.current;
    previous.current = streak;
    if (streak.last_read_date !== today || before.last_read_date === today) return;
    const key = "devfeed:streak-celebrated:" + owner;
    try {
      if (sessionStorage.getItem(key) === today) return;
      sessionStorage.setItem(key, today);
    } catch {
      // The mounted account boundary still prevents repeats when storage is unavailable.
    }
    const announce = () =>
      setCelebration(
        getStreakProgress(streak, today).earned.includes(streak.current_days)
          ? streak.current_days + " days. Milestone earned!"
          : streak.longest_days > before.longest_days
            ? streak.current_days + " days. A new personal best!"
            : "Day " + streak.current_days + " counted.",
      );
    announce();
  }, [streak, today, owner]);
  useEffect(() => {
    if (!celebration) return;
    const timer = setTimeout(() => setCelebration(undefined), 4000);
    return () => clearTimeout(timer);
  }, [celebration]);

  const state = getStreakProgress(streak, today);
  const week = useQuery({
    queryKey: ["reader", owner, revision, "reading-week", today, streak.last_read_date],
    enabled: open,
    staleTime: 60_000,
    retry: false,
    queryFn: async ({ signal }) =>
      validateReadingWeek(
        await userRequest<ReadingWeek>("settings/reading-week", {
          signal: AbortSignal.any([signal, AbortSignal.timeout(15_000)]),
        }),
      ),
  });
  const continuing = state.current > 0;
  const returning = !continuing && streak.longest_days > 0;
  const dayUnit = state.current === 1 ? "day" : "days";
  const streakLabel = state.current + " " + dayUnit;
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button
          className="reading-streak-trigger"
          data-read-today={state.readToday}
          data-celebrating={Boolean(celebration)}
          aria-label={"Reading streak: " + streakLabel}
          title="Your reading streak"
        >
          <span className="reading-streak-trigger-mark" aria-hidden="true">
            <BookOpen size={18} />
            {state.readToday && <Check size={8} className="reading-streak-trigger-check" />}
          </span>
          <span>{state.current}</span>
        </button>
      </Popover.Trigger>
      <span className="reading-streak-announcement" role="status" aria-live="polite">
        {celebration && (
          <span className="reading-streak-celebration">
            <Check size={16} aria-hidden="true" />
            {celebration}
          </span>
        )}
      </span>
      <Popover.Portal>
        <Popover.Content
          className="reading-streak-panel"
          align="end"
          sideOffset={10}
          collisionPadding={12}
          aria-label="Your reading streak"
          onCloseAutoFocus={(event) => {
            if (window.matchMedia("(max-width: 800px)").matches) {
              event.preventDefault();
              document.querySelector<HTMLButtonElement>(".topbar .user-menu-trigger")?.focus();
            }
            if (!openPicks.current) return;
            openPicks.current = false;
            // Let Radix restore focus before the requested Must Reads dialog opens.
            const scope = picksScope.current;
            scope.timer = window.setTimeout(() => {
              if (scope.mounted) window.dispatchEvent(new Event(openMustReadsEvent));
            }, 0);
          }}
        >
          <div className="reading-streak-heading">
            <span>Reading streak</span>
            <Popover.Close className="reading-streak-close" aria-label="Close reading streak">
              <X size={18} aria-hidden="true" />
            </Popover.Close>
          </div>
          <div className="reading-streak-hero">
            {continuing && (
              <strong className="reading-streak-value" aria-hidden="true">
                {state.current}
              </strong>
            )}
            <div className="reading-streak-hero-copy">
              <h2 aria-label={continuing ? streakLabel + " in a row" : undefined}>
                {continuing
                  ? dayUnit + " in a row"
                  : returning
                    ? "Start a new streak"
                    : "Start your streak"}
              </h2>
              {(continuing || returning) && (
                <span className="reading-streak-best">
                  <Trophy size={13} aria-hidden="true" />
                  {state.personalBest ? "Personal best" : "Best: " + streak.longest_days + " days"}
                </span>
              )}
            </div>
          </div>
          <section className="reading-streak-week" aria-label="Your week">
            <div className="reading-streak-section-heading">
              <h3>Last 7 days</h3>
            </div>
            {week.isPending ? (
              <p className="reading-streak-history-message" role="status">
                Loading your week…
              </p>
            ) : week.isError ? (
              <div className="reading-streak-history-message">
                <p>Couldn’t load your reading week.</p>
                <button onClick={() => void week.refetch()}>
                  <RotateCcw size={13} aria-hidden="true" /> Try again
                </button>
              </div>
            ) : week.data ? (
              <ReadingTrail week={week.data} />
            ) : null}
          </section>
          <section className="reading-streak-milestone" aria-label="Next milestone">
            <div className="reading-streak-section-heading">
              <h3>Next milestone</h3>
              <span>
                {state.remaining} {state.remaining === 1 ? "day" : "days"} to go
              </span>
            </div>
            <div
              className="reading-streak-progress"
              role="progressbar"
              aria-label="Progress to next streak milestone"
              aria-valuemin={0}
              aria-valuemax={state.next}
              aria-valuenow={state.current}
            >
              <span style={{ width: state.progress + "%" }} />
            </div>
          </section>
          {state.earned.length > 0 && (
            <section className="reading-streak-earned" aria-label="Milestones earned">
              <ul>
                {state.earned.map((day) => (
                  <li key={day}>
                    <Award size={13} aria-hidden="true" />
                    <span>{day} days</span>
                  </li>
                ))}
              </ul>
            </section>
          )}
          {!state.readToday && (
            <button
              className="reading-streak-read"
              onClick={() => {
                openPicks.current = true;
                setOpen(false);
              }}
            >
              Find today’s read <ArrowRight size={16} aria-hidden="true" />
            </button>
          )}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

function ReadingTrail({ week }: { week: ReadingWeek }) {
  return (
    <ol className="reading-streak-days">
      {week.days.map((day) => {
        const moment = new Date(day.date + "T00:00:00Z");
        const read = day.article_count > 0;
        const today = day.date === week.today;
        const label = moment.toLocaleDateString("en", {
          month: "long",
          day: "numeric",
          year: "numeric",
          timeZone: "UTC",
        });
        return (
          <li
            key={day.date}
            data-state={read ? "read" : today ? "pending" : "missed"}
            data-today={today}
            aria-label={
              label +
              ": " +
              day.article_count +
              (day.article_count === 1 ? " article" : " articles") +
              " opened" +
              (today ? ", today" : "")
            }
          >
            <time dateTime={day.date}>
              <span>{moment.toLocaleDateString("en", { weekday: "short", timeZone: "UTC" })}</span>
              <strong>{moment.getUTCDate()}</strong>
            </time>
            <span className="reading-streak-day-mark" aria-hidden="true">
              {read ? <Check size={12} /> : today ? <BookOpen size={12} /> : <span />}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
