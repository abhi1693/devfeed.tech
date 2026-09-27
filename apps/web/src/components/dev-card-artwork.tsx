"use client";

import { useId, useMemo, useState, type Ref } from "react";
import type { DevCardData } from "@/lib/dev-card";
import { cardTextLayout } from "@/lib/dev-card-text";
import brandMark from "@devfeed/theme/assets/devfeed-mark.png";
import { DevCardFrame, devCardLayout } from "./dev-card-frame";
import { CardName, CardBio, FittedText } from "./dev-card-text";
export { FittedText } from "./dev-card-text";

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
  const { names, bios } = useMemo(() => cardTextLayout(data.name, data.bio), [data.name, data.bio]);
  const nameLines = names.length;
  const bioLines = bios.length;
  return (
    <DevCardFrame
      data={{ ...data, avatar: data.avatar === failedAvatar ? null : data.avatar }}
      id={id}
      svgRef={svgRef}
      nameLines={nameLines}
      bioLines={bioLines}
      name={<CardName lines={names} />}
      bio={<CardBio lines={bios} y={devCardLayout(data, nameLines, bioLines).detailsY} />}
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
