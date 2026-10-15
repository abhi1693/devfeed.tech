"use client";

import { Input } from "@devfeed/ui/input";
import { placementSurfaces } from "@devfeed/ui/advertising-formats";

export const promotionSurfaces = placementSurfaces.map((surface) => surface.value);
export type PromotionSurface = (typeof promotionSurfaces)[number];

export function PromotionSurfaces({
  value,
  onChange,
  disabled,
  legend = "Allowed placements",
  allowed = promotionSurfaces,
}: {
  value: readonly string[];
  onChange: (value: PromotionSurface[]) => void;
  disabled?: boolean;
  legend?: string;
  allowed?: readonly PromotionSurface[];
}) {
  return (
    <fieldset disabled={disabled} className="space-y-3">
      <legend className="mb-2 text-sm font-medium">{legend}</legend>
      <div className="flex flex-wrap gap-x-6 gap-y-3">
        {placementSurfaces
          .filter((surface) => allowed.includes(surface.value))
          .map((surface) => (
            <label key={surface.value} className="flex items-center gap-2 text-sm">
              <Input
                type="checkbox"
                checked={value.includes(surface.value)}
                onChange={(event) =>
                  onChange(
                    promotionSurfaces.filter(
                      (item) =>
                        allowed.includes(item) &&
                        (item === surface.value ? event.target.checked : value.includes(item)),
                    ),
                  )
                }
              />
              {surface.label}
            </label>
          ))}
      </div>
    </fieldset>
  );
}
