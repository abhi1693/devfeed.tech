import "server-only";
import { cardImage } from "./card-image";

export function cardAvatar(url: string | null | undefined) {
  return cardImage(url, "avatar");
}
