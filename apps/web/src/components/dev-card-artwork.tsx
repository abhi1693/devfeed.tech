"use client";

import { useId, useLayoutEffect, useRef, useState, type Ref, type SVGProps } from "react";
import { cardLines, wrapCardBio, type DevCardData } from "@/lib/dev-card";
import brandMark from "@devfeed/theme/assets/devfeed-mark.png";
import { DevCardTechnologyIcon, devCardTechnologyPath } from "./dev-card-technology-icon";

function FittedText({
  children,
  maxWidth = 488,
  ...props
}: SVGProps<SVGTextElement> & { maxWidth?: number }) {
  const ref = useRef<SVGTextElement>(null);
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node?.getComputedTextLength) return;
    node.setAttribute("font-size", String(props.fontSize));
    const width = node.getComputedTextLength();
    if (width > maxWidth) {
      node.setAttribute("font-size", String((Number(props.fontSize) * maxWidth) / width));
    }
  }, [children, maxWidth, props.fontSize]);
  return (
    <text {...props} ref={ref}>
      {children}
    </text>
  );
}

function CardName({ name, onLineCount }: { name: string; onLineCount: (count: number) => void }) {
  const measure = useRef<SVGTextElement>(null);
  const [lines, setLines] = useState<string[]>([]);
  useLayoutEffect(() => {
    const node = measure.current;
    if (!node?.getComputedTextLength) return;
    const remaining = Array.from(name.trim().replace(/\s+/gu, " "));
    const next: string[] = [];
    for (const [index, width] of [480, 480].entries()) {
      let count = remaining.length;
      const fits = (value: string) => {
        node.textContent = value;
        return node.getComputedTextLength() <= width;
      };
      const overflow = !fits(remaining.join(""));
      while (
        count > 0 &&
        !fits(remaining.slice(0, count).join("") + (index === 1 && overflow ? "…" : ""))
      )
        count--;
      if (index === 0 && count < remaining.length) {
        const boundary = remaining.slice(0, count + 1).lastIndexOf(" ");
        if (boundary > 0) count = boundary;
      }
      next.push(remaining.splice(0, count).join("").trim() + (index === 1 && overflow ? "…" : ""));
      while (remaining[0] === " ") remaining.shift();
      if (!remaining.length) break;
    }
    node.textContent = "";
    setLines(next);
    onLineCount(next.length);
  }, [name, onLineCount]);
  return (
    <g
      className="dev-card-name"
      fill="var(--card-foreground)"
      fontSize="44"
      fontWeight="800"
      letterSpacing="-1.2"
    >
      <text ref={measure} visibility="hidden" aria-hidden="true" />
      {lines.map((line, index) => (
        <text key={index} x="40" y={320 + index * 47}>
          {line}
        </text>
      ))}
    </g>
  );
}

function CardBio({
  bio,
  y,
  onLineCount,
}: {
  bio: string;
  y: number;
  onLineCount: (count: number) => void;
}) {
  const measure = useRef<SVGTextElement>(null);
  const [lines, setLines] = useState<string[]>([]);
  useLayoutEffect(() => {
    const node = measure.current;
    if (!node?.getComputedTextLength) return;
    const next = wrapCardBio(bio, 480, (text) => {
      node.textContent = text;
      return node.getComputedTextLength();
    });
    node.textContent = "";
    setLines(next);
    onLineCount(next.length);
  }, [bio, onLineCount]);
  return (
    <g className="dev-card-bio" fill="var(--card-foreground)" fontSize="17">
      <text ref={measure} visibility="hidden" aria-hidden="true" />
      {lines.map((line, index) => (
        <text key={index} x="40" y={y + index * 23}>
          {line}
        </text>
      ))}
    </g>
  );
}

/** All paints come from packages/theme, including the collectible artwork. */
export function DevCardArtwork({
  data,
  svgRef,
}: {
  data: DevCardData;
  svgRef?: Ref<SVGSVGElement>;
}) {
  const id = useId().replace(/:/g, "");
  const [failedAvatar, setFailedAvatar] = useState<string | null>(null);
  const [nameLines, setNameLines] = useState(2);
  const [bioLines, setBioLines] = useState(0);
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
        {data.avatar && data.avatar !== failedAvatar && (
          <image
            data-avatar=""
            href={data.avatar}
            x="44"
            y="44"
            width="212"
            height="212"
            preserveAspectRatio="xMidYMid slice"
            clipPath={`url(#${id}-avatar)`}
            onError={() => setFailedAvatar(data.avatar)}
          />
        )}
        <CardName name={data.name} onLineCount={setNameLines} />
        <FittedText
          x="40"
          y={identityY}
          maxWidth={480}
          fill="var(--muted-foreground)"
          fontSize="15"
          fontWeight="500"
        >
          {[data.username ? `@${data.username}` : null, data.location].filter(Boolean).join(" · ")}
        </FittedText>
        <CardBio bio={data.bio} y={detailsY} onLineCount={setBioLines} />
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
                  <FittedText
                    x="56"
                    y="38"
                    textAnchor="middle"
                    maxWidth={96}
                    fill="var(--secondary-foreground)"
                    fontSize="16"
                    fontWeight="600"
                  >
                    {cardLines(technology, 20, 1)[0]}
                  </FittedText>
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
                <FittedText
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
                </FittedText>
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
          <image
            data-brand-mark=""
            href={brandMark.src}
            x="392"
            y={footerY}
            width="42"
            height="42"
          />
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
