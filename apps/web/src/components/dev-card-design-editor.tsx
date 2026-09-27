"use client";

import type { CSSProperties } from "react";
import { cardThemes, cardAccents, cardThemeTokens } from "@devfeed/theme/dev-card";
import type { DevCardSettings } from "@/lib/user";

export function DevCardDesignEditor({
  value,
  onChange,
}: {
  value: DevCardSettings | undefined;
  onChange: (value: DevCardSettings) => void;
}) {
  function update(patch: Partial<DevCardSettings>) {
    onChange({
      stats: ["current_streak", "longest_streak", "total_reading_days"],
      ...value,
      ...patch,
    });
  }
  return (
    <section className="profile-direct-section" aria-label="Dev Card design">
      <div className="profile-direct-section-heading">
        <h3>Dev Card design</h3>
      </div>
      <fieldset className="dev-card-theme-options">
        <legend>Choose a theme</legend>
        {cardThemes.map((theme) => (
          <label key={theme.id} style={cardThemeTokens(theme.id, value?.accent) as CSSProperties}>
            <input
              type="radio"
              name="dev-card-theme"
              value={theme.id}
              checked={(value?.theme ?? "classic") === theme.id}
              onChange={() => update({ theme: theme.id })}
            />
            <span className={`dev-card-theme-sample dev-card-theme-${theme.id}`} aria-hidden="true">
              <span />
            </span>
            <strong>{theme.name}</strong>
            <small>{theme.description}</small>
          </label>
        ))}
      </fieldset>
      <fieldset className="dev-card-accent-options">
        <legend>Card motion</legend>
        {(["static", "animated"] as const).map((motion) => (
          <label key={motion}>
            <input
              type="radio"
              name="dev-card-motion"
              value={motion}
              checked={(value?.motion ?? "static") === motion}
              onChange={() => update({ motion })}
            />
            {motion === "static" ? "Static" : "Animated"}
          </label>
        ))}
      </fieldset>
      <p className="dev-card-content-hint">
        Motion appears in previews and supported SVG embeds. PNG downloads and social previews stay
        static. Reduced-motion preferences are respected.
      </p>
      <fieldset className="dev-card-accent-options">
        <legend>Accent color</legend>
        {cardAccents.map((accent) => (
          <label key={accent.id}>
            <input
              type="radio"
              name="dev-card-accent"
              value={accent.id}
              checked={(value?.accent ?? "default") === accent.id}
              onChange={() => update({ accent: accent.id })}
            />
            <span aria-hidden="true" style={{ background: accent.color }} />
            {accent.name}
          </label>
        ))}
      </fieldset>
    </section>
  );
}
