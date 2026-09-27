import { cardMotionCss, classicCardDots } from "@devfeed/theme/dev-card-motion";
import { DevCardThemeArt } from "./dev-card-theme-art";
import { cardThemeTokens } from "@devfeed/theme/dev-card";
import type { CSSProperties } from "react";
import type { ComponentType, ReactNode, Ref, SVGProps } from "react";
import { cardLines, type DevCardData } from "@/lib/dev-card";
import { DevCardTechnologyIcon } from "./dev-card-technology-icon";
import { devCardStatIcons } from "./dev-card-stat-icons";

export function devCardLayout(data: DevCardData, nameLines: number, bioLines: number) {
  const identityY = 350 + (nameLines - 1) * 47;
  const detailsY = identityY + (data.username || data.location ? 34 : 8);
  const chipsY = detailsY + (bioLines ? bioLines * 23 + 4 : 0);
  let technologyRow = 0;
  let technologyX = 40;
  const technologyPositions = data.technologies.map((technology) => {
    const width = technology.logoUrl ? 64 : 112;
    if (technologyX > 40 && technologyX + width > 520) {
      technologyRow += 1;
      technologyX = 40;
    }
    const position = { x: technologyX, y: chipsY + technologyRow * 74, width };
    technologyX += width + 10;
    return position;
  });
  const contentBottom = data.technologies.length
    ? technologyPositions.at(-1)!.y + 64
    : bioLines
      ? detailsY + (bioLines - 1) * 23 + 6
      : identityY;
  const statsY = contentBottom + 26;
  const height = data.stats.length ? statsY + 112 : contentBottom + 36;
  return { identityY, detailsY, chipsY, technologyPositions, statsY, height };
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
  failedTechnologyLogos = new Set<string>(),
  onTechnologyImageError,
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
  failedTechnologyLogos?: Set<string>;
  onTechnologyImageError?: (id: string) => void;
}) {
  const layoutData = {
    ...data,
    technologies: data.technologies.map((technology) =>
      failedTechnologyLogos.has(technology.id) ? { ...technology, logoUrl: null } : technology,
    ),
  };
  const { identityY, technologyPositions, statsY, height } = devCardLayout(
    layoutData,
    nameLines,
    bioLines,
  );
  const statsColumnWidth = data.stats.length ? 480 / data.stats.length : 0;
  return (
    <svg
      ref={svgRef}
      className="dev-card-artwork"
      data-card-motion={data.motion}
      data-card-theme={data.theme}
      data-card-accent={data.accent}
      style={cardThemeTokens(data.theme, data.accent) as CSSProperties}
      xmlns="http://www.w3.org/2000/svg"
      viewBox={`0 0 560 ${height}`}
      width="560"
      height={height}
      role="img"
      aria-labelledby={`${id}-title ${id}-description`}
    >
      <title id={`${id}-title`}>{`Dev card for ${data.name}`}</title>
      <desc id={`${id}-description`}>
        {data.username ? `@${data.username}. ` : ""}
        {data.bio}
        {data.technologies.length
          ? ` Technologies: ${data.technologies.map((technology) => technology.name).join(", ")}.`
          : ""}
        {data.stats.map((stat) => ` ${stat.label.toLowerCase()}: ${stat.value}.`).join("")}
      </desc>
      {data.motion === "animated" && <style data-card-motion-style="">{cardMotionCss}</style>}
      <defs>
        <filter id={`${id}-brand-tone`}>
          <feComponentTransfer>
            <feFuncR type="linear" slope="1.7" />
            <feFuncG type="linear" slope="1.7" />
            <feFuncB type="linear" slope="1.7" />
          </feComponentTransfer>
        </filter>
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
          <g className="dev-card-motion-layer">
            {/* Scattered decorative dots, not a contribution/activity visualization. */}
            <g
              className="dev-card-grid"
              aria-hidden="true"
              visibility={data.theme === "classic" ? "visible" : "hidden"}
            >
              {classicCardDots.map((dot, index) => (
                <rect
                  key={index}
                  className="dev-card-dot"
                  x={dot.x}
                  y={dot.y}
                  width="14"
                  height="14"
                  rx="3"
                  fill={["var(--chart-1)", "var(--chart-5)", "var(--chart-6)"][dot.color]}
                  opacity={dot.opacity}
                  style={{ animationDuration: dot.duration, animationDelay: dot.delay }}
                />
              ))}
            </g>
            <DevCardThemeArt theme={data.theme} id={id} />
          </g>
        </g>
        <g className="dev-card-brand" aria-label="DevFeed">
          <path d="M384 20H540V76H416Q400 76 400 60V36Q400 20 384 20Z" fill="var(--card)" />
          <image
            data-brand-mark=""
            href={brandHref}
            x="414"
            y="33"
            width="30"
            height="30"
            filter={
              data.theme === "terminal" || data.theme === "aurora"
                ? `url(#${id}-brand-tone)`
                : undefined
            }
          />
          <text
            x="446"
            y="55"
            textAnchor="start"
            fill="var(--card-foreground)"
            fontSize="20"
            fontWeight="700"
            letterSpacing="-.6"
          >
            devfeed.
          </text>
        </g>
        {data.avatar && (
          <g data-avatar-frame="">
            <rect x="36" y="36" width="228" height="228" rx="34" fill="var(--card)" />
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
          </g>
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
                aria-label={technology.name}
                transform={`translate(${technologyPositions[index].x} ${technologyPositions[index].y})`}
              >
                <title>{technology.name}</title>
                <rect
                  width={technologyPositions[index].width}
                  height="64"
                  rx="12"
                  fill={
                    technology.logoUrl && !failedTechnologyLogos.has(technology.id)
                      ? "var(--logo-background)"
                      : "var(--secondary)"
                  }
                />
                {technology.logoUrl && !failedTechnologyLogos.has(technology.id) && (
                  <DevCardTechnologyIcon
                    technology={technology}
                    onError={() => onTechnologyImageError?.(technology.id)}
                  />
                )}
                <Text
                  x={technologyPositions[index].width / 2}
                  y="38"
                  textAnchor="middle"
                  maxWidth={96}
                  fill={
                    technology.logoUrl && !failedTechnologyLogos.has(technology.id)
                      ? "var(--logo-foreground)"
                      : "var(--secondary-foreground)"
                  }
                  fontSize="16"
                  fontWeight="600"
                  visibility={
                    technology.logoUrl && !failedTechnologyLogos.has(technology.id)
                      ? "hidden"
                      : "visible"
                  }
                  data-technology-fallback={technology.id}
                >
                  {cardLines(technology.name, 20, 1)[0]}
                </Text>
              </g>
            ))}
          </g>
        )}
        {data.stats.length > 0 && (
          <g className="dev-card-stats" transform={`translate(0 ${statsY - 530})`}>
            <path d="M40 530H520" stroke="var(--border)" />
            {data.stats.map((stat, index) => {
              const Icon = devCardStatIcons[stat.id];
              const value = new Intl.NumberFormat("en", {
                notation: "compact",
                maximumFractionDigits: 1,
              }).format(stat.value);
              // Initial placement for standalone SVGs; the preview refines this
              // against its rendered font before paint and image export.
              const valueWidth = Math.min(
                statsColumnWidth - 46,
                Array.from(value).reduce((width, char) => width + (char === "." ? 9 : 20), 0),
              );
              const pairX = (statsColumnWidth - 34 - valueWidth) / 2;
              return (
                <g key={stat.id} transform={`translate(${40 + index * statsColumnWidth} 0)`}>
                  <Icon
                    data-stat-icon={stat.id}
                    x={pairX}
                    y="550"
                    width="24"
                    height="24"
                    stroke="var(--chart-1)"
                    strokeWidth={1.75}
                    aria-hidden="true"
                  />
                  <Text
                    x={pairX + 34}
                    y="576"
                    maxWidth={statsColumnWidth - 46}
                    fill="var(--card-foreground)"
                    fontSize="34"
                    fontWeight="700"
                    letterSpacing="-.8"
                    data-stat-center={statsColumnWidth / 2}
                  >
                    {value}
                  </Text>
                  <Text
                    x={statsColumnWidth / 2}
                    textAnchor="middle"
                    y="605"
                    maxWidth={statsColumnWidth - 12}
                    fill="var(--muted-foreground)"
                    fontSize="19"
                    fontWeight="400"
                  >
                    {stat.label}
                  </Text>
                </g>
              );
            })}
          </g>
        )}
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
