export function partnerSyncInterval(minutes: number): string {
  const [value, unit] =
    minutes % 1440 === 0
      ? [minutes / 1440, "day"]
      : minutes % 60 === 0
        ? [minutes / 60, "hour"]
        : [minutes, "minute"];
  return `Every ${value} ${unit}${value === 1 ? "" : "s"}`;
}
