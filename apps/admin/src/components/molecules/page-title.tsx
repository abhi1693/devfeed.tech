"use client";

import { useEffect } from "react";
import { pageTitle } from "@/lib/page-titles";

/** Enhance route metadata with the name already fetched by the current page. */
export function PageTitle({ title }: { title: string }) {
  useEffect(() => {
    const previous = document.title;
    const current = pageTitle(title);
    document.title = current;
    return () => {
      if (document.title === current) document.title = previous;
    };
  }, [title]);
  return null;
}
