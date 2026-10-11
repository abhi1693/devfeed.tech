"use client";

import { Input } from "@/components/atoms/input";

export const promotionSurfaces = ["article", "topic", "feed"] as const;
export type PromotionSurface = (typeof promotionSurfaces)[number];
const labels: Record<PromotionSurface, string> = {
  article: "Article pages",
  topic: "Topic and category pages",
  feed: "Reader feeds",
};

export function PromotionSurfaces({
  value,
  onChange,
  disabled,
  legend = "Allowed placements",
}: {
  value: readonly string[];
  onChange: (value: PromotionSurface[]) => void;
  disabled?: boolean;
  legend?: string;
}) {
  return (
    <fieldset disabled={disabled} className="space-y-3">
      <legend className="mb-2 text-sm font-medium">{legend}</legend>
      <div className="flex flex-wrap gap-x-6 gap-y-3">
        {promotionSurfaces.map((surface) => (
          <label key={surface} className="flex items-center gap-2 text-sm">
            <Input
              type="checkbox"
              checked={value.includes(surface)}
              onChange={(event) =>
                onChange(
                  promotionSurfaces.filter((item) =>
                    item === surface ? event.target.checked : value.includes(item),
                  ),
                )
              }
            />
            {labels[surface]}
          </label>
        ))}
      </div>
    </fieldset>
  );
}
