import type { ComponentProps } from "react";
import { Input } from "./input";

export type UrlInputProps = Omit<ComponentProps<typeof Input>, "type">;

/** Shared native URL validation and editing behavior for URL-based fields. */
export function UrlInput(props: UrlInputProps) {
  return <Input spellCheck={false} autoCapitalize="none" {...props} type="url" />;
}
