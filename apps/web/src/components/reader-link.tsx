import NextLink from "next/link";
import type { ComponentProps } from "react";

/** Reader destinations load on navigation, never just because a link is visible. */
export default function ReaderLink(props: ComponentProps<typeof NextLink>) {
  return <NextLink {...props} prefetch={false} />;
}
