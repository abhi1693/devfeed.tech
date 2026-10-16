import type { ComponentProps } from "react";
import { navigate, resolveExtensionLink } from "./navigation";

type Props = ComponentProps<"a"> & {
  href: string;
  prefetch?: boolean | null;
  scroll?: boolean;
  replace?: boolean;
  onNavigate?: (event: { preventDefault: () => void }) => void;
};
export default function Link({
  href,
  prefetch: _prefetch,
  scroll,
  replace,
  onClick,
  onNavigate,
  ...props
}: Props) {
  const link = resolveExtensionLink(href);
  const local = link.destination !== null;
  return (
    <a
      {...props}
      href={link.href}
      target={props.target ?? (local ? undefined : "_blank")}
      rel={props.rel ?? (local ? undefined : "noopener noreferrer")}
      onClick={(event) => {
        onClick?.(event);
        if (
          !local ||
          event.defaultPrevented ||
          event.button !== 0 ||
          event.metaKey ||
          event.ctrlKey ||
          event.shiftKey ||
          event.altKey ||
          (props.target && props.target !== "_self")
        )
          return;
        event.preventDefault();
        let cancelled = false;
        onNavigate?.({ preventDefault: () => (cancelled = true) });
        if (cancelled) return;
        const destination = link.destination!;
        navigate(
          destination.kind === "page" && scroll === false
            ? { ...destination, scroll: "preserve" }
            : destination,
          replace ? "replace" : "push",
        );
      }}
    />
  );
}
