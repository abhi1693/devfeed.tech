"use client";

import { readerIsWebsite } from "@/lib/reader-runtime";

/* eslint-disable @next/next/no-img-element -- Static badge artwork is shared with the extensions. */
const featuredOn = [
  {
    name: "Nick Launches",
    href: "https://nicklaunches.com/products/devfeed/",
    light: "/featured/nick-launches-light.png",
    dark: "/featured/nick-launches-dark.png",
    width: 244,
    height: 56,
  },
];

export function FeaturedOn() {
  if (!readerIsWebsite()) return null;
  return (
    <section className="sidebar-featured" aria-label="Featured on">
      <div className="sidebar-featured-badges">
        {featuredOn.map((badge) => (
          <a key={badge.name} href={badge.href} target="_blank" rel="noopener noreferrer">
            <img
              className="featured-light"
              src={badge.light}
              alt={`DevFeed on ${badge.name}`}
              width={badge.width}
              height={badge.height}
            />
            <img
              className="featured-dark"
              src={badge.dark}
              alt={`DevFeed on ${badge.name}`}
              width={badge.width}
              height={badge.height}
            />
          </a>
        ))}
      </div>
    </section>
  );
}
