import Link from "next/link";
import { LegalContact, LegalPage, type LegalSection } from "@/components/legal-page";
import { canonicalUrl, pageMetadata } from "@/lib/metadata";

const description =
  "The terms for using DevFeed to discover developer news, follow topics and sources, and share source suggestions.";
export function generateMetadata() {
  return pageMetadata("Terms of Service", description, canonicalUrl("/legal/terms"));
}

const sections: LegalSection[] = [
  {
    id: "using-devfeed",
    title: "Using DevFeed",
    content: (
      <>
        <p>
          These terms govern your use of DevFeed at devfeed.tech. By using the service, you agree to
          these terms. If you do not agree, please stop using it. You must be legally able to accept
          these terms or use the service with the permission and supervision of a parent or legal
          guardian where required.
        </p>
        <p>
          DevFeed helps you discover developer news, tutorials and releases. Browsing is available
          without an account. An optional account lets you save preferences, follow topics and
          sources, like articles and suggest sources.
        </p>
      </>
    ),
  },
  {
    id: "accounts",
    title: "Your account",
    content: (
      <>
        <p>
          Sign-in is provided through our identity service and supported identity providers. Keep
          your sign-in credentials secure, provide accurate information and contact us if you
          believe your account has been compromised. You are responsible for activity you authorize
          through your account.
        </p>
        <p>
          Do not impersonate others, share access to evade restrictions or use another person’s
          account without permission. Signing out of DevFeed ends your DevFeed session; it may not
          sign you out of your identity provider or other websites.
        </p>
      </>
    ),
  },
  {
    id: "publisher-content",
    title: "Publisher content and AI summaries",
    content: (
      <>
        <p>
          Linked articles, publisher names, logos, images and other third-party material belong to
          their respective owners. Display on DevFeed does not imply ownership, partnership or
          endorsement. DevFeed previews help you find the original work; the publisher’s website
          remains the source of the complete article and its applicable terms.
        </p>
        <p>
          We use automated systems, including AI, to summarize, classify and organize public
          content. Summaries, labels, recommendations and source assessments can be incomplete,
          inaccurate or outdated. Check the original source before relying on them, particularly
          before making security, financial, legal or other consequential decisions.
        </p>
        <p>
          You are responsible for reviewing third-party terms and privacy practices when you follow
          an external link. We do not control the availability, content or conduct of those
          websites.
        </p>
        <p>
          DevFeed’s source code is available under the{" "}
          <a href="https://github.com/abhi1693/devfeed.tech/blob/master/LICENSE">MIT license</a>.
          These service terms do not limit the rights that license grants. Third-party dependencies
          and publisher content retain their own licenses and rights.
        </p>
      </>
    ),
  },
  {
    id: "contributions",
    title: "Source suggestions and contributions",
    content: (
      <>
        <p>
          Submit only information you are entitled to share. Do not include confidential
          information, credentials, private feed URLs or someone else’s personal information in a
          source suggestion. Suggestions are reviewed and may be rejected, edited or removed.
        </p>
        <p>
          You retain any rights you hold in your contributions. By submitting them, you give DevFeed
          permission to store, review, process and display them as needed to operate and improve the
          service, including automated source assessment. This permission does not transfer
          ownership of a publisher’s content.
        </p>
      </>
    ),
  },
  {
    id: "acceptable-use",
    title: "Acceptable use",
    content: (
      <>
        <p>Use DevFeed lawfully and respectfully. You must not:</p>
        <ul>
          <li>Attempt unauthorized access, introduce malware or interfere with the service.</li>
          <li>Evade rate limits, access controls or other abuse protections.</li>
          <li>
            Manipulate article counts, likes, recommendations or source review through fraudulent
            activity.
          </li>
          <li>Submit unlawful, infringing, deceptive or abusive material.</li>
          <li>
            Use automated requests in a way that disrupts availability or extracts private data.
          </li>
        </ul>
        <p>
          Public machine-readable pages are available for discovery and reading. Their availability
          does not grant rights to third-party content or permission to bypass access limits.
        </p>
      </>
    ),
  },
  {
    id: "privacy",
    title: "Privacy",
    content: (
      <>
        <p>
          Our <Link href="/legal/privacy">Privacy Policy</Link> explains the information we process,
          including account data, preferences, cookies, analytics and original-article clicks.
          Accepting these terms is not a substitute for any separate consent required by applicable
          law.
        </p>
      </>
    ),
  },
  {
    id: "availability",
    title: "Availability and account restrictions",
    content: (
      <>
        <p>
          We may change, suspend or discontinue features, and may restrict accounts or requests to
          address abuse, security issues, legal requirements or violations of these terms. Where
          reasonable and lawful, we will explain account restrictions and consider requests for
          review.
        </p>
        <p>
          You may stop using DevFeed at any time. Contact us to request deletion of your DevFeed
          account. Deleting a DevFeed account does not delete your GitHub account or accounts with
          other providers.
        </p>
      </>
    ),
  },
  {
    id: "disclaimers",
    title: "Disclaimers and responsibility",
    content: (
      <>
        <p>
          To the extent permitted by applicable law, DevFeed is provided “as is” and “as available,”
          without warranties of uninterrupted availability, accuracy or fitness for a particular
          purpose. We cannot guarantee that every article, summary or recommendation will be correct
          or useful.
        </p>
        <p>
          To the extent permitted by applicable law, DevFeed is not responsible for indirect or
          consequential losses arising from use of the service or reliance on third-party content.
          Nothing in these terms excludes liability that cannot lawfully be excluded, or limits
          mandatory consumer rights.
        </p>
      </>
    ),
  },
  {
    id: "copyright",
    title: "Copyright concerns and disputes",
    content: (
      <>
        <p>
          If you believe material on DevFeed infringes your rights, email us with the affected
          DevFeed URL, the original work, an explanation of your rights and a way to contact you. We
          will review the report and may correct or remove the material.
        </p>
        <p>
          Please contact us first with other concerns so we can try to resolve them. These terms do
          not require arbitration or waive rights or remedies available under applicable law.
        </p>
      </>
    ),
  },
  {
    id: "changes",
    title: "Changes to these terms",
    content: (
      <>
        <p>
          We may update these terms as the service changes. The effective date appears at the top of
          this page. For material changes, we will provide additional notice where appropriate or
          required by law. Changes apply from their stated effective date, subject to applicable
          law.
        </p>
      </>
    ),
  },
  { id: "contact", title: "Contact", content: <LegalContact /> },
];

export default function TermsPage() {
  return (
    <LegalPage
      title="Terms of Service"
      path="/legal/terms"
      introduction={description}
      sections={sections}
    />
  );
}
