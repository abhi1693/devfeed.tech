/** Only application-owned reason codes are rendered; provider text is never displayed. */
export function signInFailure(reason: unknown): string | undefined {
  if (!reason) return undefined;
  switch (reason) {
    case "login_cancelled":
      return "Sign-in was cancelled. Try again when you’re ready.";
    case "login_expired":
      return "This sign-in expired or was replaced by a newer attempt. Start again to continue.";
    case "login_unavailable":
      return "Sign-in is temporarily unavailable. Wait a moment, then try again.";
    default:
      return "Sign-in didn’t finish. Please try again.";
  }
}
