import "server-only";
import { cachedCardImage, cardImage } from "./card-image";

export function cardAvatar(url: string | null | undefined) {
  return cardImage(url, "avatar");
}

export function cachedCardAvatar(url: string | null | undefined) {
  return cachedCardImage(url, "avatar");
}

export function warmCardAvatar(url: string | null | undefined) {
  return cardImage(url, "avatar");
}
