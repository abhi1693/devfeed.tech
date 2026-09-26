"use client";
import Link from "next/link";
import { useEffect } from "react";
import { BookOpen, Flame, MapPin, Pencil, Trophy } from "lucide-react";
import { ProfileAvatar } from "./profile-avatar";
import { ProfileLinkIcon } from "./profile-link-icon";
import { CatalogIcon } from "./catalog-icon";
import { useUser } from "./user-account";
import { profileLinkLabel } from "@/lib/profile-links";
import { safeExternalUrl } from "@/lib/feed-query";
import type { UserProfile } from "@/lib/user";
import type { PublicReadingActivity } from "@/lib/server/public-dev-card";
import { trackEvent } from "@/lib/analytics";

const sections = {
  primary: "Uses regularly",
  learning: "Currently learning",
  hobby: "Side projects",
  past: "Previously used",
} as const;

export function PublicUserProfile({
  profile,
  activity,
}: {
  profile: UserProfile;
  activity: PublicReadingActivity | null;
}) {
  const { profile: ownProfile } = useUser();
  const owner = Boolean(ownProfile?.username && ownProfile.username === profile.username);
  const name = profile.display_name || profile.username || "DevFeed reader";
  const stats = profile.reading_streak;
  const links = (profile.links ?? []).filter((link) => safeExternalUrl(link.url));
  useEffect(() => {
    const timer = window.setTimeout(() => trackEvent("dev_card_view", {}), 0);
    return () => window.clearTimeout(timer);
  }, [profile.username]);
  return (
    <div className={`public-profile${profile.stack?.length ? "" : " public-profile-no-stack"}`}>
      <header className="public-profile-hero">
        <div className="public-profile-cover" aria-hidden="true">
          <span className="public-profile-cover-orbit" />
          <span className="public-profile-cover-grid" />
        </div>
        <div className="public-profile-identity">
          <div className="public-profile-avatar-row">
            <ProfileAvatar name={name} url={profile.avatar_url} />
            {owner && (
              <Link className="button" href="/settings/profile">
                <Pencil size={15} />
                Edit profile
              </Link>
            )}
          </div>
          <p className="public-profile-handle">@{profile.username}</p>
          <h1>{name}</h1>
          {profile.bio && <p className="public-profile-bio">{profile.bio}</p>}
          {profile.location && (
            <div className="public-profile-meta">
              <span>
                <MapPin size={15} />
                {profile.location}
              </span>
            </div>
          )}
          {links.length > 0 && (
            <nav className="public-profile-links" aria-label="Profile links">
              {links.map((link) => (
                <a
                  key={link.url}
                  href={link.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label={link.label || profileLinkLabel(link.url) || "Website"}
                >
                  <ProfileLinkIcon url={link.url} />
                  <span>{link.label || profileLinkLabel(link.url) || "Website"}</span>
                </a>
              ))}
            </nav>
          )}
        </div>
      </header>
      <div className="public-profile-main">
        {profile.about && (
          <section className="public-profile-section" aria-labelledby="profile-about-heading">
            <p className="public-profile-eyebrow">IN THEIR OWN WORDS</p>
            <h2 id="profile-about-heading">About</h2>
            <p className="public-profile-about">{profile.about}</p>
          </section>
        )}
        {Boolean(stats || activity?.days?.length) && (
          <section className="public-profile-section" aria-labelledby="profile-reading-heading">
            <h2 id="profile-reading-heading">Reading journey</h2>
            {stats && (
              <dl className="public-profile-stats">
                {[
                  { label: "Current streak", value: stats.current_days, Icon: Flame, unit: "days" },
                  {
                    label: "Longest streak",
                    value: stats.longest_days,
                    Icon: Trophy,
                    unit: "days",
                  },
                  { label: "Reading days", value: stats.total_days, Icon: BookOpen, unit: "total" },
                ].map(({ label, value, Icon, unit }) => (
                  <div key={label}>
                    <dt>
                      <Icon size={16} />
                      {label}
                    </dt>
                    <dd>
                      {value ?? 0}
                      <span>{unit}</span>
                    </dd>
                  </div>
                ))}
              </dl>
            )}
            {activity?.days?.length ? (
              <ReadingCalendar activity={activity} />
            ) : (
              <p className="public-profile-muted">Reading activity is currently unavailable.</p>
            )}
          </section>
        )}
      </div>
      {!!profile.stack?.length && (
        <aside className="public-profile-aside" aria-label="Technologies">
          {!!profile.stack?.length && (
            <section
              className="public-profile-section public-profile-stack-panel"
              aria-labelledby="profile-stack-heading"
            >
              <h2 id="profile-stack-heading">Stack &amp; technologies</h2>
              {Object.entries(sections).map(([key, label]) => {
                const items = profile.stack!.filter((item) => (item.section || "primary") === key);
                return (
                  items.length > 0 && (
                    <div className="public-profile-stack-group" key={key}>
                      <h3>{label}</h3>
                      <div className="public-profile-stack">
                        {items.map((item) => (
                          <Link
                            key={item.topic_id || item.name}
                            href={`/topics/${encodeURIComponent(item.slug || item.name.toLowerCase())}`}
                            className="public-profile-technology"
                          >
                            <CatalogIcon url={item.logo_url} kind={item.kind} />
                            <span>
                              <strong>{item.name}</strong>
                              <small>
                                {item.kind.replaceAll("_", " ")}
                                {item.since_year ? ` · Since ${item.since_year}` : ""}
                              </small>
                            </span>
                          </Link>
                        ))}
                      </div>
                    </div>
                  )
                );
              })}
            </section>
          )}
        </aside>
      )}
    </div>
  );
}

function ReadingCalendar({ activity }: { activity: PublicReadingActivity }) {
  const offset = new Date(`${activity.days[0].date}T00:00:00Z`).getUTCDay();
  const months = activity.days.flatMap((day, index) => {
    const date = new Date(`${day.date}T00:00:00Z`);
    return date.getUTCDate() === 1
      ? [
          {
            label: date.toLocaleDateString("en", { month: "short", timeZone: "UTC" }),
            column: Math.floor((offset + index) / 7) + 1,
          },
        ]
      : [];
  });
  const total = activity.days.reduce((sum, day) => sum + day.article_count, 0);
  return (
    <div className="public-profile-calendar">
      <div className="public-profile-calendar-heading">
        <strong>{activity.year}</strong>
        <span>{total} article opens · UTC</span>
      </div>
      <div
        className="public-profile-calendar-scroll"
        tabIndex={0}
        role="region"
        aria-label={`Reading activity for ${activity.year}`}
      >
        <div className="public-profile-months" aria-hidden="true">
          {months.map((month) => (
            <span key={month.label} style={{ gridColumn: `${month.column} / span 4` }}>
              {month.label}
            </span>
          ))}
        </div>
        <div className="public-profile-days">
          {Array.from({ length: offset }, (_, index) => (
            <span key={`pad-${index}`} aria-hidden="true" />
          ))}
          {activity.days.map((day) => (
            <span
              key={day.date}
              className="public-profile-day"
              data-level={Math.min(day.article_count, 4)}
              title={`${day.date}: ${day.article_count} article opens`}
              aria-label={`${day.date}: ${day.article_count} article opens`}
            />
          ))}
        </div>
      </div>
      {total === 0 && (
        <p className="public-profile-muted">No reading activity yet in {activity.year}.</p>
      )}
      <div className="public-profile-calendar-key">
        <span>Less</span>
        {[0, 1, 2, 3, 4].map((level) => (
          <i key={level} className="public-profile-day" data-level={level} />
        ))}
        <span>More</span>
      </div>
    </div>
  );
}
