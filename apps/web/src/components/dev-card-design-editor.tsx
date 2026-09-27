"use client";

import { useId, type CSSProperties } from "react";
import { Check, CirclePause, Play } from "lucide-react";
import { ChoiceGroup, ChoiceItem } from "@devfeed/ui/choice-controls";
import { cardThemes, cardAccents, cardThemeTokens, type CardTheme } from "@devfeed/theme/dev-card";
import { classicCardDots } from "@devfeed/theme/dev-card-motion";
import type { DevCardSettings } from "@/lib/user";
import { DevCardThemeArt } from "./dev-card-theme-art";

function ThemeSample({ theme, accent }: { theme: CardTheme; accent: DevCardSettings["accent"] }) {
  const id = useId().replace(/:/g, "");
  return (
    <svg
      className="dev-card-theme-sample"
      viewBox="20 20 520 244"
      preserveAspectRatio="xMidYMid slice"
      style={cardThemeTokens(theme, accent) as CSSProperties}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={`${id}-color`} x1="0" y1="0" x2="1" y2="1">
          <stop stopColor="var(--chart-1)" />
          <stop offset="1" stopColor="var(--chart-5)" />
        </linearGradient>
      </defs>
      <rect x="20" y="20" width="520" height="244" fill="var(--secondary)" />
      <rect x="20" y="20" width="520" height="244" fill={`url(#${id}-color)`} opacity=".12" />
      {theme === "classic" ? (
        classicCardDots.map((dot, index) => (
          <rect
            key={index}
            x={dot.x}
            y={dot.y}
            width="14"
            height="14"
            rx="3"
            fill={["var(--chart-1)", "var(--chart-5)", "var(--chart-6)"][dot.color]}
            opacity={dot.opacity}
          />
        ))
      ) : (
        <g transform="translate(-150 0)">
          <DevCardThemeArt theme={theme} id={id} />
        </g>
      )}
    </svg>
  );
}

export function DevCardDesignEditor({
  value,
  onChange,
}: {
  value: DevCardSettings | undefined;
  onChange: (value: DevCardSettings) => void;
}) {
  const id = useId();
  function update(patch: Partial<DevCardSettings>) {
    onChange({
      stats: ["current_streak", "longest_streak", "total_reading_days"],
      ...value,
      ...patch,
    });
  }
  return (
    <section
      className="profile-direct-section dev-card-editor-section"
      aria-label="Dev Card design"
    >
      <div className="dev-card-editor-heading">
        <h3>Dev Card design</h3>
        <p>Make your card feel like you.</p>
      </div>
      <div className="dev-card-editor-field">
        <span className="dev-card-field-label" id={`${id}-theme`}>
          Theme
        </span>
        <ChoiceGroup
          className="dev-card-theme-options"
          aria-labelledby={`${id}-theme`}
          value={value?.theme ?? "classic"}
          onValueChange={(theme) => update({ theme: theme as CardTheme })}
        >
          {cardThemes.map((theme) => (
            <ChoiceItem
              key={theme.id}
              value={theme.id}
              className="dev-card-theme-choice"
              aria-label={theme.name}
            >
              <ThemeSample theme={theme.id} accent={value?.accent} />
              <span className="dev-card-theme-caption">
                <span>
                  <strong>{theme.name}</strong>
                  <small>{theme.description}</small>
                </span>
                <span className="dev-card-choice-check" aria-hidden="true">
                  <Check size={12} strokeWidth={3} />
                </span>
              </span>
            </ChoiceItem>
          ))}
        </ChoiceGroup>
      </div>
      <div className="dev-card-editor-field dev-card-motion-setting">
        <div>
          <span className="dev-card-field-label" id={`${id}-motion`}>
            Motion
          </span>
          <p>Bring your card to life.</p>
        </div>
        <ChoiceGroup
          className="dev-card-motion-options"
          aria-labelledby={`${id}-motion`}
          value={value?.motion ?? "animated"}
          onValueChange={(motion) => update({ motion: motion as "static" | "animated" })}
        >
          <ChoiceItem value="static">
            <CirclePause size={14} aria-hidden="true" />
            Static
          </ChoiceItem>
          <ChoiceItem value="animated">
            <Play size={14} aria-hidden="true" />
            Animated
          </ChoiceItem>
        </ChoiceGroup>
      </div>
      <div className="dev-card-editor-field">
        <span className="dev-card-field-label" id={`${id}-accent`}>
          Accent color
        </span>
        <ChoiceGroup
          className="dev-card-accent-options"
          aria-labelledby={`${id}-accent`}
          value={value?.accent ?? "default"}
          onValueChange={(accent) => update({ accent: accent as DevCardSettings["accent"] })}
        >
          {cardAccents.map((accent) => (
            <ChoiceItem key={accent.id} value={accent.id} aria-label={accent.name}>
              <span
                className="dev-card-accent-dot"
                aria-hidden="true"
                style={{ background: accent.color }}
              />
              {accent.id === "default" ? "Default" : accent.name}
              <Check className="dev-card-accent-check" size={12} aria-hidden="true" />
            </ChoiceItem>
          ))}
        </ChoiceGroup>
      </div>
      <p className="dev-card-editor-note">
        Image downloads stay still. Motion follows the viewer’s accessibility preferences.
      </p>
    </section>
  );
}
