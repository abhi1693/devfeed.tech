import { permanentRedirect } from "next/navigation";

export default function Preferences() {
  permanentRedirect("/settings/topics");
}
