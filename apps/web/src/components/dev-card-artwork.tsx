"use client";

import { useId, useLayoutEffect, useRef, useState, type Ref, type SVGProps } from "react";
import { wrapCardBio, type DevCardData } from "@/lib/dev-card";
import brandMark from "@devfeed/theme/assets/devfeed-mark.png";
import { DevCardFrame, devCardLayout } from "./dev-card-frame";

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
  const [failedTechnologyLogos, setFailedTechnologyLogos] = useState<Set<string>>(() => new Set());
  const [nameLines, setNameLines] = useState(2);
  const [bioLines, setBioLines] = useState(0);
  return (
    <DevCardFrame
      data={{ ...data, avatar: data.avatar === failedAvatar ? null : data.avatar }}
      id={id}
      svgRef={svgRef}
      nameLines={nameLines}
      bioLines={bioLines}
      name={<CardName name={data.name} onLineCount={setNameLines} />}
      bio={
        <CardBio
          bio={data.bio}
          y={devCardLayout(data, nameLines, bioLines).detailsY}
          onLineCount={setBioLines}
        />
      }
      Text={FittedText}
      brandHref={brandMark.src}
      onAvatarError={() => setFailedAvatar(data.avatar)}
      failedTechnologyLogos={failedTechnologyLogos}
      onTechnologyImageError={(id) =>
        setFailedTechnologyLogos((previous) => new Set(previous).add(id))
      }
    />
  );
}
