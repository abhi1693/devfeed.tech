import type { DevCardData } from "@/lib/dev-card";

export function DevCardTechnologyIcon({
  technology,
  onError,
}: {
  technology: DevCardData["technologies"][number];
  onError?: () => void;
}) {
  if (!technology.logoUrl) return null;
  return (
    <image
      x="14"
      y="14"
      width="36"
      height="36"
      href={technology.logoUrl}
      preserveAspectRatio="xMidYMid meet"
      data-technology-logo={technology.id}
      onError={onError}
    />
  );
}
