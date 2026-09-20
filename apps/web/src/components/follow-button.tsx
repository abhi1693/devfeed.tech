import { readerWebsiteLink } from "@/lib/reader-runtime";
import { Check, LoaderCircle, Plus } from "lucide-react";
import { MotionIcon } from "./motion-icon";

export function FollowButton({
  signedIn,
  loading,
  followed,
  pending,
  disabled,
  returnTo,
  onClick,
  labels = { follow: "Follow", following: "Following" },
}: {
  signedIn: boolean;
  loading: boolean;
  followed: boolean;
  pending: boolean;
  disabled: boolean;
  returnTo: string;
  onClick: () => void;
  labels?: { follow: string; following: string };
}) {
  if (!loading && !signedIn)
    return (
      <a
        className="button follow-button"
        {...readerWebsiteLink(`/api/v1/user/auth/login?return_to=${encodeURIComponent(returnTo)}`)}
      >
        <Plus size={16} aria-hidden="true" />
        {labels.follow}
      </a>
    );

  return (
    <button
      className="button follow-button"
      type="button"
      aria-pressed={followed}
      disabled={disabled}
      onClick={onClick}
    >
      <MotionIcon value={pending ? "saving" : followed ? "following" : "idle"}>
        {pending ? (
          <LoaderCircle size={16} className="settings-spinner" aria-hidden="true" />
        ) : followed ? (
          <Check size={16} aria-hidden="true" />
        ) : (
          <Plus size={16} aria-hidden="true" />
        )}
      </MotionIcon>
      {pending ? "Saving…" : followed ? labels.following : labels.follow}
    </button>
  );
}
