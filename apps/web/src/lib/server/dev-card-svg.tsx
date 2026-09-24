import "server-only";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { renderToStaticMarkup } from "react-dom/server.edge";
import type { SVGProps } from "react";
import { DevCardFrame, devCardLayout } from "@/components/dev-card-frame";
import { cardLines, devCardData, wrapCardBio } from "@/lib/dev-card";
import type { UserProfile } from "@/lib/user";
import { cardAvatar } from "./card-avatar";

// Conservative glyph widths keep standalone SVGs readable without browser layout.
// The interactive preview refines these measurements against its actual font.
function measure(value: string, size: number) {
  return Array.from(value).reduce(
    (width, char) =>
      width +
      size *
        (/\s/.test(char)
          ? 0.3
          : /[ilI.,'!]/.test(char)
            ? 0.3
            : /[MW@]/.test(char)
              ? 0.9
              : char.codePointAt(0)! > 255
                ? 1
                : 0.62),
    0,
  );
}
function Text({
  children,
  maxWidth = 488,
  ...props
}: SVGProps<SVGTextElement> & { maxWidth?: number }) {
  const size = Number(props.fontSize ?? 16);
  const width = measure(String(children ?? ""), size);
  return (
    <text {...props} fontSize={width > maxWidth ? (size * maxWidth) / width : size}>
      {children}
    </text>
  );
}

let assets: Promise<{ tokens: Record<string, string>; brand: string }> | undefined;
function cardAssets() {
  return (assets ??= Promise.all([
    readFile(path.join(process.cwd(), "../../packages/theme/tokens.css"), "utf8"),
    readFile(path.join(process.cwd(), "../../packages/theme/assets/devfeed-mark.png")),
  ])
    .then(([css, png]) => ({
      tokens: Object.fromEntries(
        Array.from(css.split(".dark")[0].matchAll(/(--[\w-]+):\s*([^;]+);/g), (match) => [
          match[1],
          match[2].trim(),
        ]),
      ),
      brand: `data:image/png;base64,${png.toString("base64")}`,
    }))
    .catch((error) => {
      assets = undefined;
      throw error;
    }));
}

export async function renderDevCardSvg(profile: UserProfile) {
  const { tokens, brand } = await cardAssets();
  const data = {
    ...devCardData(profile, { name: profile.username ?? "DevFeed reader" }),
    avatar: await cardAvatar(profile.avatar_url),
  };
  const names = cardLines(data.name, 17, 2);
  const bios = wrapCardBio(data.bio, 480, (value) => measure(value, 17));
  const { detailsY } = devCardLayout(data, names.length, bios.length);
  const svg = renderToStaticMarkup(
    <DevCardFrame
      data={data}
      id="devfeed-card"
      nameLines={names.length}
      bioLines={bios.length}
      brandHref={brand}
      Text={Text}
      name={
        <g
          className="dev-card-name"
          fill="var(--card-foreground)"
          fontSize="44"
          fontWeight="800"
          letterSpacing="-1.2"
        >
          {names.map((line, index) => (
            <Text key={index} x="40" y={320 + index * 47} fontSize="44" maxWidth={480}>
              {line}
            </Text>
          ))}
        </g>
      }
      bio={
        <g className="dev-card-bio" fill="var(--card-foreground)" fontSize="17">
          {bios.map((line, index) => (
            <text key={index} x="40" y={detailsY + index * 23}>
              {line}
            </text>
          ))}
        </g>
      }
    />,
  );
  return svg.replace(
    /(fill|stroke|stop-color|font-family)="var\((--[\w-]+)\)"/g,
    (_match, attribute: string, key: string) => {
      if (!(key in tokens)) throw new Error(`Missing card theme token: ${key}`);
      return `${attribute}="${tokens[key]}"`;
    },
  );
}
