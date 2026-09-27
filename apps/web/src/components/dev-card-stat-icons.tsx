/* SVG geometry from Lucide 1.48.0.
ISC License

Copyright (c) 2026 Lucide Icons and Contributors

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
*/
import { createElement, type SVGProps } from "react";
import type { DevCardStat } from "@/lib/user";

// Plain SVG keeps the exact editor icons usable in server-rendered image exports.
const iconData = {
  current_streak: {
    name: "flame",
    node: [
      [
        "path",
        {
          d: "M12 3q1 4 4 6.5t3 5.5a1 1 0 0 1-14 0 5 5 0 0 1 1-3 1 1 0 0 0 5 0c0-2-1.5-3-1.5-5q0-2 2.5-4",
          key: "1slcih",
        },
      ],
    ],
  },
  longest_streak: {
    name: "trophy",
    node: [
      [
        "path",
        {
          d: "M10 14.66V17a1 1 0 0 1-1 1 2 2 0 0 0-2 2v2",
          key: "pwuv1l",
        },
      ],
      [
        "path",
        {
          d: "M14 14.66V17a1 1 0 0 0 1 1 2 2 0 0 1 2 2v2",
          key: "1y54w1",
        },
      ],
      [
        "path",
        {
          d: "M17.916 10H19.5A2.5 2.5 0 0 0 22 7.5V5a1 1 0 0 0-1-1h-3",
          key: "e30mpu",
        },
      ],
      [
        "path",
        {
          d: "M4 22h16",
          key: "57wxv0",
        },
      ],
      [
        "path",
        {
          d: "M6 9a6 6 0 0 0 12 0V3a1 1 0 0 0-1-1H7a1 1 0 0 0-1 1z",
          key: "1mhfuq",
        },
      ],
      [
        "path",
        {
          d: "M6.084 10H4.5A2.5 2.5 0 0 1 2 7.5V5a1 1 0 0 1 1-1h3",
          key: "i0yafy",
        },
      ],
    ],
  },
  total_reading_days: {
    name: "calendar-days",
    node: [
      [
        "path",
        {
          d: "M8 2v3",
          key: "1ioesn",
        },
      ],
      [
        "path",
        {
          d: "M16 2v3",
          key: "otl347",
        },
      ],
      [
        "rect",
        {
          x: "3",
          y: "3",
          width: "18",
          height: "18",
          rx: "2",
          key: "h1oib",
        },
      ],
      [
        "path",
        {
          d: "M3 9h18",
          key: "1pudct",
        },
      ],
      [
        "path",
        {
          d: "M8 13h.01",
          key: "1sbv64",
        },
      ],
      [
        "path",
        {
          d: "M12 13h.01",
          key: "y0uutt",
        },
      ],
      [
        "path",
        {
          d: "M16 13h.01",
          key: "wip0gl",
        },
      ],
      [
        "path",
        {
          d: "M8 17h.01",
          key: "p3bg7i",
        },
      ],
      [
        "path",
        {
          d: "M12 17h.01",
          key: "p32p05",
        },
      ],
      [
        "path",
        {
          d: "M16 17h.01",
          key: "ql8jdd",
        },
      ],
    ],
  },
} as const;
type Props = SVGProps<SVGSVGElement> & { size?: number };
function StatIcon({ stat, size = 24, ...props }: Props & { stat: DevCardStat }) {
  const icon = iconData[stat];
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`lucide lucide-${icon.name}`}
      {...props}
    >
      {icon.node.map(([tag, attributes]) => createElement(tag, attributes))}
    </svg>
  );
}
export const devCardStatIcons = {
  current_streak: (props: Props) => <StatIcon stat="current_streak" {...props} />,
  longest_streak: (props: Props) => <StatIcon stat="longest_streak" {...props} />,
  total_reading_days: (props: Props) => <StatIcon stat="total_reading_days" {...props} />,
};
