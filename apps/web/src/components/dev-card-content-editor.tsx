"use client";

import { Checkbox } from "@devfeed/ui/choice-controls";
import { devCardStatIcons } from "./dev-card-stat-icons";
import type { DevCardSettings, DevCardStat, UserStack } from "@/lib/user";

const stats = [
  { id: "current_streak", label: "Current reading streak", icon: devCardStatIcons.current_streak },
  { id: "longest_streak", label: "Best reading streak", icon: devCardStatIcons.longest_streak },
  {
    id: "total_reading_days",
    label: "Total reading days",
    icon: devCardStatIcons.total_reading_days,
  },
] as const;

export function DevCardContentEditor({
  value,
  stack,
  onChange,
}: {
  value: DevCardSettings | undefined;
  stack: UserStack[];
  onChange: (value: DevCardSettings) => void;
}) {
  const available = stack.filter((item) => item.section !== "past");
  const technologies = value?.technologies ?? available.map((item) => item.topic_id);
  const selectedStats: DevCardStat[] = value?.stats ?? stats.map(({ id }) => id);
  return (
    <section
      className="profile-direct-section dev-card-editor-section"
      aria-label="Dev Card content"
    >
      <div className="dev-card-editor-heading">
        <h3>On your card</h3>
        <p>Choose the details you want to share.</p>
      </div>
      <div className="dev-card-content-options">
        <fieldset aria-label="Technologies shown on Dev Card">
          <legend>Featured technologies</legend>
          <p className="dev-card-content-description">Highlight your stack.</p>
          <div className="dev-card-technology-choices">
            {available.length ? (
              available.map((item) => {
                const checked = technologies.includes(item.topic_id);
                return (
                  <label
                    key={item.topic_id}
                    className="dev-card-technology-choice"
                    data-selected={checked}
                  >
                    <Checkbox
                      checked={checked}
                      onCheckedChange={(selected) =>
                        onChange({
                          ...value,
                          technologies:
                            selected === true
                              ? [...technologies, item.topic_id]
                              : technologies.filter((id) => id !== item.topic_id),
                          stats: selectedStats,
                        })
                      }
                    />
                    <span>{item.name}</span>
                  </label>
                );
              })
            ) : (
              <p className="dev-card-content-empty">
                Add technologies to your stack to feature them here.
              </p>
            )}
          </div>
        </fieldset>
        <fieldset aria-label="Stats shown on Dev Card">
          <legend>Reading stats</legend>
          <p className="dev-card-content-description">Show your reading habits.</p>
          <div className="dev-card-stat-choices">
            {stats.map(({ id, label, icon: Icon }) => (
              <label key={id} className="dev-card-stat-choice">
                <Icon size={16} aria-hidden="true" />
                <span>{label}</span>
                <Checkbox
                  checked={selectedStats.includes(id)}
                  onCheckedChange={(selected) =>
                    onChange({
                      ...value,
                      technologies: value?.technologies ?? null,
                      stats:
                        selected === true
                          ? [...selectedStats, id]
                          : selectedStats.filter((item) => item !== id),
                    })
                  }
                />
              </label>
            ))}
          </div>
        </fieldset>
      </div>
      <p className="dev-card-editor-note">
        Preview changes live. Save your profile when you’re ready.
      </p>
    </section>
  );
}
