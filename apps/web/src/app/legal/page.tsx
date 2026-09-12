import { permanentRedirect } from "next/navigation";

export default function LegalIndex() {
  permanentRedirect("/legal/terms");
}
