import type { MetadataRoute } from "next";
import brandMark from "@devfeed/theme/assets/devfeed-mark.png";
import appleIcon from "@devfeed/theme/assets/devfeed-icon-180.png";

export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: "DevFeed",
    short_name: "DevFeed",
    description: "Developer news and your daily must-read.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    icons: [
      { src: appleIcon.src, sizes: "180x180", type: "image/png" },
      { src: brandMark.src, sizes: "1254x1254", type: "image/png" },
    ],
  };
}
