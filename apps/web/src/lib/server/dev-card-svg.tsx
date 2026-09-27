import "server-only";
import { cardThemeTokens } from "@devfeed/theme/dev-card";
import { readFile } from "node:fs/promises";
import path from "node:path";
import sharp from "sharp";
import { renderToStaticMarkup } from "react-dom/server.edge";
import { CardName, CardBio, FittedText } from "@/components/dev-card-text";
import { cardTextLayout } from "@/lib/dev-card-text";
import { DevCardFrame, devCardLayout } from "@/components/dev-card-frame";
import { devCardData } from "@/lib/dev-card";
import type { UserProfile } from "@/lib/user";
import { cachedCardAvatar, warmCardAvatar } from "./card-avatar";
import { cachedCardLogos, warmCardLogos } from "./card-logos";

let assets: Promise<{ tokens: Record<string, string>; brand: string }> | undefined;
function cardAssets() {
  return (assets ??= Promise.all([
    readFile(path.join(process.cwd(), "../../packages/theme/tokens.css"), "utf8"),
    readFile(path.join(process.cwd(), "../../packages/theme/assets/devfeed-mark.png")),
  ])
    .then(async ([css, png]) => ({
      tokens: Object.fromEntries(
        Array.from(css.split(".dark")[0].matchAll(/(--[\w-]+):\s*([^;]+);/g), (match) => [
          match[1],
          match[2].trim(),
        ]),
      ),
      brand: `data:image/webp;base64,${(await sharp(png).resize(96, 96).webp({ lossless: true }).toBuffer()).toString("base64")}`,
    }))
    .catch((error) => {
      assets = undefined;
      throw error;
    }));
}

export async function renderDevCardSvg(profile: UserProfile) {
  const { tokens, brand } = await cardAssets();
  const data = devCardData(profile, { name: profile.username ?? "DevFeed reader" });
  // Finish bounded image loading before serializing so the first request is complete.
  await Promise.all([
    warmCardAvatar(profile.avatar_url),
    warmCardLogos(data.technologies.map((technology) => technology.logoUrl)),
  ]);
  const avatar = cachedCardAvatar(profile.avatar_url);
  const logos = cachedCardLogos(data.technologies.map((technology) => technology.logoUrl));
  data.avatar = avatar;
  data.technologies = data.technologies.map((technology) => ({
    ...technology,
    logoUrl: (technology.logoUrl && logos.get(technology.logoUrl)) || null,
  }));
  const { names, bios } = cardTextLayout(data.name, data.bio);
  const { detailsY } = devCardLayout(data, names.length, bios.length);
  const svg = renderToStaticMarkup(
    <DevCardFrame
      data={data}
      id="devfeed-card"
      nameLines={names.length}
      bioLines={bios.length}
      brandHref={brand}
      Text={FittedText}
      name={<CardName lines={names} />}
      bio={<CardBio lines={bios} y={detailsY} />}
    />,
  );
  const selectedTokens = { ...tokens, ...cardThemeTokens(data.theme, data.accent) };
  return svg.replace(
    /(fill|stroke|stop-color|font-family)="var\((--[\w-]+)\)"/g,
    (_match, attribute: string, key: string) => {
      if (!(key in selectedTokens)) throw new Error(`Missing card theme token: ${key}`);
      return `${attribute}="${selectedTokens[key]}"`;
    },
  );
}
