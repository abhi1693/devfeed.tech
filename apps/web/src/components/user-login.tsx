/* eslint-disable @next/next/no-html-link-for-pages -- Authentication needs full browser navigation, never prefetch. */
import Link from "next/link";
import { LogIn } from "lucide-react";

export function UserLoginError() {
  return (
    <section className="empty-state login-error">
      <LogIn size={28} />
      <h1>Sign-in couldn’t finish</h1>
      <p>Please try again. You can continue reading without signing in.</p>
      <div className="account-actions">
        <a className="button primary" href="/api/v1/user/auth/login">
          Try sign-in again
        </a>
        <Link className="button" href="/">
          Back to articles
        </Link>
      </div>
    </section>
  );
}
