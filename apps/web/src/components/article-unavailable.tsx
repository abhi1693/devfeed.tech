import type { ReactNode } from "react";

export function ArticleUnavailable({ children }: { children: ReactNode }) {
  return (
    <section className="empty-state" role="alert">
      <h1>Couldn’t load the article</h1>
      <p>Please try again or return to your feed.</p>
      {children}
    </section>
  );
}
