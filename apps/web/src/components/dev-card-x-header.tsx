"use client";

import { useId, useLayoutEffect, useRef, useState, type CSSProperties, type Ref } from "react";
import { cardThemeTokens } from "@devfeed/theme/dev-card";
import { classicCardDots } from "@devfeed/theme/dev-card-motion";
import brandMark from "@devfeed/theme/assets/devfeed-mark.png";
import { cardLines, type DevCardData } from "@/lib/dev-card";
import { FittedText } from "./dev-card-artwork";
import { DevCardThemeArt } from "./dev-card-theme-art";
import { DevCardTechnologyIcon } from "./dev-card-technology-icon";
import { devCardStatIcons } from "./dev-card-stat-icons";

/** A dedicated cover composition with space for X's lower-left profile photo. */
export function DevCardXHeader({
  data,
  svgRef,
}: {
  data: DevCardData;
  svgRef?: Ref<SVGSVGElement>;
}) {
  const id = `header-${useId().replace(/:/g, "")}`;
  const [failedLogos, setFailedLogos] = useState<Set<string>>(() => new Set());
  const content = useRef<SVGGElement>(null);
  useLayoutEffect(() => {
    const group = content.current;
    if (!group?.getBBox) return;
    let height = 0;
    for (const section of group.querySelectorAll<SVGGElement>("[data-header-section]")) {
      const box = section.getBBox();
      if (!box.width || !box.height) continue;
      if (height) height += Number(section.getAttribute("data-section-gap"));
      section.setAttribute("transform", `translate(0 ${height - box.y})`);
      height += box.height;
    }
    // Center the actual rendered content below the brand, closing gaps when
    // sections are hidden. Keep dense profiles inside the same crop-safe area.
    const scale = Math.min(1, 316 / height);
    group.setAttribute(
      "transform",
      `translate(550 ${270 - (height * scale) / 2}) scale(${scale}) translate(-550 0)`,
    );
  }, [data]);
  const selectedTechnologies = data.technologies.map((technology) => {
    const label = cardLines(technology.name, 18, 1)[0];
    const width = technology.logoUrl && !failedLogos.has(technology.id) ? 64 : 100;
    return { ...technology, label, width };
  });
  const technologies = selectedTechnologies.map((technology, index) => ({
    ...technology,
    x:
      550 +
      selectedTechnologies.slice(0, index).reduce((total, chip) => total + chip.width + 12, 0),
  }));
  const columnWidth = 820 / Math.max(data.stats.length, 1);
  return (
    <svg
      ref={svgRef}
      className="dev-card-x-header"
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 1500 500"
      width="1500"
      height="500"
      data-card-theme={data.theme}
      data-card-accent={data.accent}
      data-card-motion="static"
      style={cardThemeTokens(data.theme, data.accent) as CSSProperties}
      fontFamily="var(--font-family-sans)"
      role="img"
      aria-label={`X header for ${data.name}`}
      aria-describedby={`${id}-description`}
    >
      <desc id={`${id}-description`}>
        {[
          data.username && `@${data.username}`,
          data.location,
          data.bio,
          data.technologies.length
            ? `Technologies: ${data.technologies.map((item) => item.name).join(", ")}.`
            : "",
          ...data.stats.map((stat) => `${stat.label}: ${stat.value}.`),
        ]
          .filter(Boolean)
          .join(" ")}
      </desc>
      <defs>
        <radialGradient id={`${id}-glow`}>
          <stop stopColor="var(--chart-1)" stopOpacity=".65" />
          <stop offset="1" stopColor="var(--chart-1)" stopOpacity="0" />
        </radialGradient>
        <clipPath id={`${id}-decoration-clip`}>
          <rect width="520" height="500" />
        </clipPath>
        <linearGradient id={`${id}-color`} x1="0" y1="0" x2="1" y2="1">
          <stop stopColor="var(--chart-1)" />
          <stop offset="1" stopColor="var(--chart-5)" />
        </linearGradient>
        <linearGradient id={`${id}-art-fade`}>
          <stop offset=".55" stopColor="white" />
          <stop offset="1" stopColor="black" />
        </linearGradient>
        <mask id={`${id}-art-mask`}>
          <rect width="510" height="500" fill={`url(#${id}-art-fade)`} />
        </mask>
      </defs>
      <rect width="1500" height="500" fill="var(--card)" />
      <g clipPath={`url(#${id}-decoration-clip)`} aria-hidden="true">
        <ellipse
          cx="-100"
          cy="250"
          rx="680"
          ry="500"
          fill={`url(#${id}-glow)`}
          mask={`url(#${id}-art-mask)`}
        />
        <g mask={`url(#${id}-art-mask)`} aria-hidden="true" data-header-art="" opacity=".65">
          {data.theme === "classic" ? (
            <g className="dev-card-header-classic" transform="translate(-50 20) scale(1.8)">
              {classicCardDots.map((dot, index) => (
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
              ))}
            </g>
          ) : (
            <g transform="translate(-450 -30) scale(2)">
              <DevCardThemeArt theme={data.theme} id={id} />
            </g>
          )}
        </g>
      </g>
      <g className="dev-card-header-brand" aria-label="DevFeed">
        <image data-brand-mark="" href={brandMark.src} x="1224" y="77" width="36" height="36" />
        <text
          x="1262"
          y="103"
          fontSize="27"
          fontWeight="800"
          letterSpacing="-1"
          fill="var(--card-foreground)"
        >
          devfeed.
        </text>
      </g>
      <g ref={content} data-header-content="" fill="var(--card-foreground)">
        {(data.username || data.location) && (
          <g data-header-section="identity" data-section-gap="0">
            <FittedText
              x="550"
              y="112"
              maxWidth={620}
              minFontSize={18}
              fontSize="20"
              fill="var(--muted-foreground)"
            >
              {[data.username && `@${data.username}`, data.location].filter(Boolean).join(" · ")}
            </FittedText>
          </g>
        )}
        <g data-header-section="name" data-section-gap="8">
          <FittedText
            x="550"
            y="183"
            maxWidth={820}
            minFontSize={40}
            fontSize="64"
            fontWeight="800"
            letterSpacing="-2"
          >
            {cardLines(data.name, 38, 1)[0]}
          </FittedText>
        </g>
        {data.bio && (
          <g data-header-section="bio" data-section-gap="10">
            {cardLines(data.bio, 65, 3).map((line, index) => (
              <FittedText
                key={index}
                x="550"
                y={218 + index * 24}
                maxWidth={820}
                minFontSize={20}
                fontSize="20"
                fill="var(--muted-foreground)"
              >
                {line}
              </FittedText>
            ))}
          </g>
        )}
        {technologies.length > 0 && (
          <g data-header-technologies="" data-header-section="technologies" data-section-gap="12">
            {technologies.map((technology) => (
              <g key={technology.id} transform={`translate(${technology.x} 280)`}>
                <rect
                  width={technology.width}
                  height="64"
                  rx="12"
                  fill={
                    technology.logoUrl && !failedLogos.has(technology.id)
                      ? "var(--logo-background)"
                      : "var(--secondary)"
                  }
                  stroke="var(--border)"
                />
                {technology.logoUrl && !failedLogos.has(technology.id) && (
                  <DevCardTechnologyIcon
                    technology={technology}
                    onError={() =>
                      setFailedLogos((previous) => new Set(previous).add(technology.id))
                    }
                  />
                )}
                <FittedText
                  x={technology.width / 2}
                  y="38"
                  textAnchor="middle"
                  maxWidth={technology.width - 24}
                  minFontSize={16}
                  fontSize="17"
                  fontWeight="500"
                  data-technology-fallback={technology.id}
                  visibility={
                    technology.logoUrl && !failedLogos.has(technology.id) ? "hidden" : "visible"
                  }
                  fill={
                    technology.logoUrl && !failedLogos.has(technology.id)
                      ? "var(--logo-foreground)"
                      : "var(--secondary-foreground)"
                  }
                >
                  {technology.label}
                </FittedText>
              </g>
            ))}
          </g>
        )}
        {data.stats.length > 0 && (
          <g data-header-stats="" data-header-section="stats" data-section-gap="16">
            <path d="M550 342H1370" stroke="var(--border)" />
            {data.stats.map((stat, index) => {
              const Icon = devCardStatIcons[stat.id];
              return (
                <g key={stat.id} transform={`translate(${550 + index * columnWidth} 0)`}>
                  <Icon
                    x="0"
                    y="362"
                    width="24"
                    height="24"
                    stroke="var(--chart-1)"
                    aria-hidden="true"
                  />
                  <FittedText
                    x="36"
                    y="385"
                    maxWidth={columnWidth - 48}
                    fontSize="34"
                    fontWeight="700"
                  >
                    {Intl.NumberFormat("en", {
                      notation: "compact",
                      maximumFractionDigits: 1,
                    }).format(stat.value)}
                  </FittedText>
                  <text x="0" y="415" fontSize="18" fill="var(--muted-foreground)">
                    {stat.label}
                  </text>
                </g>
              );
            })}
          </g>
        )}
      </g>
    </svg>
  );
}
