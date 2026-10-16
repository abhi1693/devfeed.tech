import { createLucideIcon } from "lucide-react";

// Lucide geometry (ISC), combined into independent subpaths to avoid repeated SVG nodes in lists.
export const CardPlus = createLucideIcon("Plus", [
  ["path", { d: "M5 12h14M12 5v14", key: "plus" }],
]);

export const CardEye = createLucideIcon("Eye", [
  [
    "path",
    {
      d: "M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0M15 12a3 3 0 1 0-6 0 3 3 0 1 0 6 0Z",
      key: "eye",
    },
  ],
]);
