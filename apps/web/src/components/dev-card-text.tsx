import type { SVGProps } from "react";
import { fitCardText } from "@/lib/dev-card-text";

/** Pure SVG text: browser previews and server embeds use the same fitting rules. */
export function FittedText({
  children,
  maxWidth = 488,
  minFontSize = 0,
  ...props
}: SVGProps<SVGTextElement> & { maxWidth?: number; minFontSize?: number }) {
  const fitted = fitCardText(
    String(children ?? ""),
    {
      size: Number(props.fontSize ?? 16),
      weight: Number(props.fontWeight ?? 400),
      spacing: Number(props.letterSpacing ?? 0),
    },
    maxWidth,
    minFontSize,
  );
  return (
    <text {...props} style={{ ...props.style, fontKerning: "none" }} fontSize={fitted.size}>
      {fitted.text}
    </text>
  );
}

export function CardName({ lines }: { lines: string[] }) {
  return (
    <g
      className="dev-card-name"
      fill="var(--card-foreground)"
      fontSize="44"
      fontWeight="800"
      letterSpacing="-1.2"
      style={{ fontKerning: "none" }}
    >
      {lines.map((line, index) => (
        <text key={index} x="40" y={320 + index * 47}>
          {line}
        </text>
      ))}
    </g>
  );
}

export function CardBio({ lines, y }: { lines: string[]; y: number }) {
  return (
    <g
      className="dev-card-bio"
      fill="var(--card-foreground)"
      fontSize="17"
      style={{ fontKerning: "none" }}
    >
      {lines.map((line, index) => (
        <text key={index} x="40" y={y + index * 23}>
          {line}
        </text>
      ))}
    </g>
  );
}
