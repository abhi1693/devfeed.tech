"use client";

import { useEffect, useState } from "react";
import { Popover } from "radix-ui";
import { BookOpen, Check, X } from "lucide-react";
import { useUser } from "./user-account";

function utcDay() {
  return new Date().toISOString().slice(0, 10);
}

export function ReadingStreak() {
  const { user, profile } = useUser();
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
  const streak = profile?.reading_streak;
  if (!user || !streak) return null;
  const yesterday = new Date(Date.parse(today) - 86_400_000).toISOString().slice(0, 10);
  const readToday = streak.last_read_date === today;
  const current = readToday || streak.last_read_date === yesterday ? streak.current_days : 0;
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button
          className="reading-streak-trigger"
          data-read-today={readToday}
          aria-label={`Reading streak: ${current} ${current === 1 ? "day" : "days"}`}
          title="Your reading rhythm"
        >
          <BookOpen size={18} aria-hidden="true" />
          <span>{current}</span>
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          className="reading-streak-panel"
          align="end"
          sideOffset={10}
          collisionPadding={12}
          aria-label="Your reading rhythm"
        >
          <div className="reading-streak-heading">
            <span>Your reading rhythm</span>
            <Popover.Close className="reading-streak-close" aria-label="Close reading streak">
              <X size={16} aria-hidden="true" />
            </Popover.Close>
          </div>
          <div className="reading-streak-current">
            <strong>{current}</strong>
            <span>
              {current === 1 ? "day" : "days"}
              <br />
              in a row
            </span>
            <BookOpen size={32} aria-hidden="true" />
          </div>
          <p className="reading-streak-today" data-complete={readToday}>
            {readToday && <Check size={14} aria-hidden="true" />}
            {readToday
              ? "Today counted"
              : current
                ? "Keep it going. Open an article today."
                : "Start with an article today."}
          </p>
          <dl className="reading-streak-stats">
            <div>
              <dt>Best streak</dt>
              <dd>
                {streak.longest_days ?? 0}
                <span> days</span>
              </dd>
            </div>
            <div>
              <dt>Reading days</dt>
              <dd>
                {streak.total_days ?? 0}
                <span> total</span>
              </dd>
            </div>
          </dl>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
