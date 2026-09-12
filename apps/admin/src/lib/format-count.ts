const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });

export function formatCompactCount(value: number) {
  return compact.format(value).toLowerCase();
}
