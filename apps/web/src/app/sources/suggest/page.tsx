import { SourceSuggestion } from "@/components/source-suggestion";
import { UserShell } from "@/components/user-shell";
export const metadata = { title: "Suggest a source" };
export default function SuggestSourcePage() {
  return <UserShell section="sources"><SourceSuggestion /></UserShell>;
}
