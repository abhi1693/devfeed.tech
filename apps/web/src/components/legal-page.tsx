import Link from "next/link";
import type { ReactNode } from "react";
import { UserShell } from "./user-shell";
import { JsonLd } from "./json-ld";
import { canonicalUrl } from "@/lib/metadata";
import { legalEmail, legalPages, legalSocialUrl, legalUpdated } from "@/lib/legal";

export type LegalSection = { id: string; title: string; content: ReactNode };

export function LegalContact() {
  return (
    <p>
      Contact DevFeed at <a href={`mailto:${legalEmail}`}>{legalEmail}</a>. You can also find us on
      X at <a href={legalSocialUrl}>@abhi16_93</a>. Please use email for account, privacy or
      copyright requests, and do not post personal information publicly.
    </p>
  );
}

export function LegalPage({
  title,
  path,
  introduction,
  sections,
}: {
  title: string;
  path: string;
  introduction: string;
  sections: LegalSection[];
}) {
  return (
    <UserShell section="legal">
      <JsonLd
        data={{
          "@context": "https://schema.org",
          "@type": "WebPage",
          name: title,
          url: canonicalUrl(path),
          description: introduction,
          dateModified: legalUpdated,
          isPartOf: { "@id": `${canonicalUrl("/")}#website` },
        }}
      />
      <article className="legal-document">
        <header className="legal-header">
          <nav className="legal-page-links" aria-label="Legal documents">
            {legalPages.map((page) => (
              <Link
                key={page.path}
                href={page.path}
                aria-current={path === page.path ? "page" : undefined}
              >
                {page.title}
              </Link>
            ))}
          </nav>
          <h1>{title}</h1>
          <p>{introduction}</p>
          <p className="legal-updated">
            Effective and last updated: <time dateTime={legalUpdated}>September 13, 2026</time>
          </p>
        </header>
        <nav className="legal-contents" aria-label="On this page">
          <h2>On this page</h2>
          <ol>
            {sections.map((section) => (
              <li key={section.id}>
                <a href={`#${section.id}`}>{section.title}</a>
              </li>
            ))}
          </ol>
        </nav>
        {sections.map((section, index) => (
          <section
            key={section.id}
            id={section.id}
            className="legal-section"
            aria-labelledby={`${section.id}-title`}
          >
            <h2 id={`${section.id}-title`}>
              {index + 1}. {section.title}
            </h2>
            {section.content}
          </section>
        ))}
      </article>
    </UserShell>
  );
}
