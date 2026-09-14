import type { ComponentProps } from "react";
import { linkDestination, useRouter } from "./navigation";

type Props = ComponentProps<"a"> & {
  href: string;
  prefetch?: boolean | null;
  scroll?: boolean;
  replace?: boolean;
};
export default function Link({
  href,
  prefetch: _prefetch,
  scroll,
  replace,
  onClick,
  ...props
}: Props) {
  const router = useRouter();
  const destination = linkDestination(href);
  const local = destination.startsWith("#");
  return (
    <a
      {...props}
      href={destination}
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
        router[replace ? "replace" : "push"](href, { scroll });
      }}
    />
  );
}
