import type { ComponentType, ReactNode, Ref, SVGProps } from "react";
import { cardLines, type DevCardData } from "@/lib/dev-card";
import { DevCardTechnologyIcon, devCardTechnologyPath } from "./dev-card-technology-icon";

export function devCardLayout(data: DevCardData, nameLines: number, bioLines: number) {
  const identityY = 350 + (nameLines - 1) * 47;
  const detailsY = identityY + (data.username || data.location ? 34 : 8);
  const chipsY = detailsY + (bioLines ? bioLines * 23 + 4 : 0);
  const technologyWidths = data.technologies.map((name) =>
    devCardTechnologyPath(name) ? 64 : 112,
  );
  const contentBottom = data.technologies.length
    ? chipsY + 64
    : bioLines
      ? detailsY + (bioLines - 1) * 23 + 6
      : identityY;
  const statsY = contentBottom + 26;
  const footerY = (data.stats.length ? statsY + 64 : contentBottom) + 24;
  const height = footerY + 58;
  return { identityY, detailsY, chipsY, technologyWidths, statsY, footerY, height };
}

export function DevCardFrame({
  data,
  id,
  svgRef,
  name,
  bio,
  nameLines,
  bioLines,
  Text,
  brandHref,
  onAvatarError,
}: {
  data: DevCardData;
  id: string;
  svgRef?: Ref<SVGSVGElement>;
  name: ReactNode;
  bio: ReactNode;
  nameLines: number;
  bioLines: number;
  Text: ComponentType<SVGProps<SVGTextElement> & { maxWidth?: number }>;
  brandHref: string;
  onAvatarError?: () => void;
}) {
  const { identityY, chipsY, technologyWidths, statsY, footerY, height } = devCardLayout(
    data,
    nameLines,
    bioLines,
  );
  return (
    <svg
      ref={svgRef}
      className="dev-card-artwork"
      xmlns="http://www.w3.org/2000/svg"
      viewBox={`0 0 560 ${height}`}
      width="560"
      height={height}
      role="img"
      aria-labelledby={`${id}-title ${id}-description`}
    >
      <title id={`${id}-title`}>Dev card for {data.name}</title>
      <desc id={`${id}-description`}>
        {data.username ? `@${data.username}. ` : ""}
        {data.bio}
        {data.technologies.length ? ` Technologies: ${data.technologies.join(", ")}.` : ""}
        {data.stats.map((stat) => ` ${stat.label.toLowerCase()}: ${stat.value}.`).join("")}
      </desc>
      <defs>
        <linearGradient id={`${id}-color`} x1="0" y1="0" x2="1" y2="1">
          <stop stopColor="var(--chart-1)" />
          <stop offset="1" stopColor="var(--chart-5)" />
        </linearGradient>
        <clipPath id={`${id}-clip`}>
          <rect x="1" y="1" width="558" height={height - 2} rx="24" />
        </clipPath>
        <clipPath id={`${id}-art`}>
          <rect x="20" y="20" width="520" height="244" rx="16" />
        </clipPath>
        <clipPath id={`${id}-avatar`}>
          <rect x="44" y="44" width="212" height="212" rx="26" />
        </clipPath>
      </defs>
      <g clipPath={`url(#${id}-clip)`} fontFamily="var(--font-family-sans)">
        <rect width="560" height={height} fill="var(--card)" />
        <g clipPath={`url(#${id}-art)`}>
          <rect x="20" y="20" width="520" height="244" fill="var(--secondary)" />
          <rect x="20" y="20" width="520" height="244" fill={`url(#${id}-color)`} opacity=".12" />
          {/* Fixed geometric repeat, not a contribution/activity visualization. */}
          <g className="dev-card-grid" aria-hidden="true">
            {Array.from({ length: 275 }, (_, index) => {
              const row = Math.floor(index / 25);
              const col = index % 25;
              return (
                <rect
                  key={index}
                  x={30 + col * 20}
                  y={30 + row * 20}
                  width="14"
                  height="14"
                  rx="3"
                  fill={["var(--chart-1)", "var(--chart-5)", "var(--chart-6)"][(row + col) % 3]}
                  opacity={[0.12, 0.2, 0.4, 1, 0.4, 0.2, 0.12][(row + col) % 7]}
                />
              );
            })}
          </g>
        </g>
        <rect x="36" y="36" width="228" height="228" rx="34" fill="var(--card)" />
        <rect x="44" y="44" width="212" height="212" rx="26" fill="var(--secondary)" />
        <text
          x="150"
          y="174"
          textAnchor="middle"
          fill="var(--secondary-foreground)"
          fontSize="68"
          fontWeight="700"
          letterSpacing="-2"
        >
          {data.initials}
        </text>
        {data.avatar && (
          <image
            data-avatar=""
            href={data.avatar}
            x="44"
            y="44"
            width="212"
            height="212"
            preserveAspectRatio="xMidYMid slice"
            clipPath={`url(#${id}-avatar)`}
            onError={onAvatarError}
          />
        )}
        {name}
        <Text
          x="40"
          y={identityY}
          maxWidth={480}
          fill="var(--muted-foreground)"
          fontSize="15"
          fontWeight="500"
        >
          {[data.username ? `@${data.username}` : null, data.location].filter(Boolean).join(" · ")}
        </Text>
        {bio}
        {data.technologies.length > 0 && (
          <g className="dev-card-technologies">
            {data.technologies.map((technology, index) => (
              <g
                key={index}
                role="img"
                aria-label={technology}
                transform={`translate(${40 + technologyWidths.slice(0, index).reduce((sum, width) => sum + width + 10, 0)} ${chipsY})`}
              >
                <title>{technology}</title>
                <rect width={technologyWidths[index]} height="64" rx="12" fill="var(--secondary)" />
                {devCardTechnologyPath(technology) ? (
                  <DevCardTechnologyIcon name={technology} />
                ) : (
                  <Text
                    x="56"
                    y="38"
                    textAnchor="middle"
                    maxWidth={96}
                    fill="var(--secondary-foreground)"
                    fontSize="16"
                    fontWeight="600"
                  >
                    {cardLines(technology, 20, 1)[0]}
                  </Text>
                )}
              </g>
            ))}
          </g>
        )}
        {data.stats.length > 0 && (
          <g className="dev-card-stats" transform={`translate(0 ${statsY - 530})`}>
            <path d="M40 530H520" stroke="var(--border)" />
            {data.stats.map((stat, index) => (
              <g key={stat.label} transform={`translate(${40 + index * 166} 0)`}>
                {index > 0 && <path d="M-20 552v40" stroke="var(--border)" />}
                <Text
                  x="0"
                  y="569"
                  maxWidth={124}
                  fill="var(--card-foreground)"
                  fontSize="30"
                  fontWeight="700"
                  letterSpacing="-1.2"
                >
                  {new Intl.NumberFormat("en", {
                    notation: "compact",
                    maximumFractionDigits: 1,
                  }).format(stat.value)}
                </Text>
                <text
                  x="0"
                  y="594"
                  fill="var(--muted-foreground)"
                  fontSize="10"
                  fontWeight="500"
                  letterSpacing=".4"
                >
                  {stat.label}
                </text>
              </g>
            ))}
          </g>
        )}
        <g className="dev-card-brand" aria-label="DevFeed">
          <image data-brand-mark="" href={brandHref} x="392" y={footerY} width="42" height="42" />
          <text
            x="520"
            y={footerY + 28}
            textAnchor="end"
            fill="var(--card-foreground)"
            fontSize="22"
            fontWeight="800"
            letterSpacing="-1.2"
          >
            devfeed.
          </text>
        </g>
      </g>
      <rect
        x="1"
        y="1"
        width="558"
        height={height - 2}
        rx="24"
        fill="none"
        stroke="var(--border)"
        strokeWidth="2"
      />
    </svg>
  );
}
