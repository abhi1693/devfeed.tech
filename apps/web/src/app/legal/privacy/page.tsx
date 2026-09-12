import Link from "next/link";
import { LegalContact, LegalPage, type LegalSection } from "@/components/legal-page";
import { canonicalUrl, pageMetadata } from "@/lib/metadata";

const description =
  "How DevFeed processes account information, preferences and usage data, and how to contact us about your privacy.";
export function generateMetadata() {
  return pageMetadata("Privacy Policy", description, canonicalUrl("/legal/privacy"));
}

const sections: LegalSection[] = [
  {
    id: "scope",
    title: "About this policy",
    content: (
      <>
        <p>
          This policy covers DevFeed at devfeed.tech and the services that support it. DevFeed is a
          developer-content discovery service. You can browse without signing in; an account is
          optional for personalization and contributions.
        </p>
        <p>
          This policy does not cover independent publishers or identity providers. Their policies
          apply when you use their services. See also our{" "}
          <Link href="/legal/terms">Terms of Service</Link>.
        </p>
      </>
    ),
  },
  {
    id: "information",
    title: "Information we process",
    content: (
      <>
        <ul>
          <li>
            <strong>Account information:</strong> an identifier from our sign-in service, your name
            and email, organization membership, and sign-in timestamps. We use ZITADEL with
            supported providers such as GitHub. DevFeed does not receive your GitHub password.
          </li>
          <li>
            <strong>Profile and preferences:</strong> a display name or photo URL you choose to
            save, followed topics and sources, likes, feed and appearance settings, and notification
            preferences.
          </li>
          <li>
            <strong>Contributions and correspondence:</strong> source suggestions, their review
            status and information you send when contacting us.
          </li>
          <li>
            <strong>Usage and operational information:</strong> original-article clicks, search
            requests, analytics events, request timing, errors and information needed to deliver and
            protect the service.
          </li>
        </ul>
        <p>
          Our infrastructure and providers necessarily process connection information such as IP
          addresses and browser request details. This is separate from DevFeed’s article-click
          records, which use account or random browser identifiers rather than storing IP addresses
          or browser fingerprints.
        </p>
      </>
    ),
  },
  {
    id: "purposes",
    title: "How we use information",
    content: (
      <>
        <p>
          We use this information to authenticate users, save settings, personalize recommendations,
          deliver in-app notifications, review source suggestions, respond to requests, measure
          useful features and keep DevFeed secure and reliable.
        </p>
        <p>
          Recommendations use signals such as follows and likes together with article freshness and
          topic relationships. You can change these signals through your preferences and likes, or
          browse the public feed without signing in.
        </p>
      </>
    ),
  },
  {
    id: "cookies",
    title: "Cookies and browser storage",
    content: (
      <>
        <p>
          DevFeed uses first-party cookies for sign-in sessions and authentication security.
          Sessions are time-limited and signing out revokes the local session. Theme and other
          interface preferences may be stored in your browser so they survive navigation or a later
          visit.
        </p>
        <p>
          When an anonymous reader opens an original article, DevFeed can set a random visitor
          cookie lasting up to 30 days. It helps deduplicate clicks and limit abuse.
          Original-article clicks are counted independently of Google Analytics; opening a preview
          is not counted as reading the original article.
        </p>
        <p>
          You can clear cookies and site storage in your browser. Blocking session cookies can
          prevent sign-in, and clearing preference storage resets the affected settings. Clearing
          cookies does not delete account information held by DevFeed.
        </p>
      </>
    ),
  },
  {
    id: "analytics",
    title: "Google Analytics",
    content: (
      <>
        <p>
          The public production website uses Google Analytics 4 to understand visits and feature
          usage. Google can receive page URLs, referrers, browser and device information, cookie
          identifiers and interaction data. Its collection and processing are described in{" "}
          <a href="https://policies.google.com/technologies/partner-sites">
            How Google uses information from sites or apps that use its services
          </a>{" "}
          and <a href="https://policies.google.com/privacy">Google’s Privacy Policy</a>.
        </p>
        <p>
          Our custom events include original-article clicks, likes, follows, source suggestions and
          saved preferences. Those event payloads do not include your name, email, account
          identifier, submitted source URL or free-text input. Standard page URLs can include search
          or filter parameters, so do not put confidential information into searches or URLs.
        </p>
        <p>
          You can manage cookies in your browser and use{" "}
          <a href="https://tools.google.com/dlpage/gaoptout">
            Google’s Analytics opt-out browser add-on
          </a>{" "}
          where supported. These controls do not disable DevFeed’s separate article-click abuse
          protection.
        </p>
      </>
    ),
  },
  {
    id: "providers",
    title: "Service providers and external content",
    content: (
      <>
        <p>
          We use infrastructure and services to run DevFeed, including Cloudflare for web delivery
          and protection, our hosted ZITADEL sign-in service, GitHub when selected for sign-in,
          Google Analytics for measurement, and our hosted notification service for in-app
          notifications.
        </p>
        <p>
          AI services, including OpenAI, process public article, source and taxonomy content to
          produce summaries, classifications and research. A submitted public source URL and its
          retrieved content may be processed during source assessment. Do not submit private or
          confidential material.
        </p>
        <p>
          Publisher images, logos and profile photo URLs may be loaded from external hosts. Those
          hosts can receive connection information when your browser requests an image. Following an
          original-article link takes you to the publisher’s service and privacy practices.
        </p>
        <p>
          Information may be processed in countries other than your own, depending on the provider.
          We may also disclose information when required by law or reasonably necessary to
          investigate abuse, protect people or defend legal rights.
        </p>
      </>
    ),
  },
  {
    id: "retention",
    title: "Retention",
    content: (
      <>
        <p>
          Account details, preferences and contributions are retained while needed to provide the
          service and handle legitimate operational or legal requirements. You can request account
          deletion by email. There is currently no self-service account-deletion control in DevFeed.
        </p>
        <p>
          Article-click deduplication records are scheduled for removal after 30 days; aggregate
          article counts can remain. Sessions, rate-limit records and caches expire. Operational
          records and backups may remain for their operational or recovery periods; deletion from
          active systems does not imply immediate erasure from every backup.
        </p>
        <p>
          Google Analytics and other independent providers apply their own retention settings and
          policies. Signing out or clearing your browser does not itself delete data already
          processed by those providers.
        </p>
      </>
    ),
  },
  {
    id: "choices",
    title: "Your choices and privacy requests",
    content: (
      <>
        <p>
          You can browse anonymously, change your profile and preferences, unfollow topics or
          sources, remove likes, adjust notifications and sign out. Email us to request access to,
          correction of or deletion of your DevFeed personal information, or to raise a privacy
          concern.
        </p>
        <p>
          Depending on the law that applies to you, you may also have rights to object to
          processing, restrict processing, receive a portable copy, withdraw consent where
          processing relies on it, or complain to a relevant supervisory authority. We may need to
          verify your identity before handling a request. Applicable exceptions and legal retention
          duties may limit what we can erase.
        </p>
        <p>
          Requests about a GitHub account or another provider’s independent processing must also be
          directed to that provider. Removing your DevFeed account does not remove those separate
          accounts.
        </p>
      </>
    ),
  },
  {
    id: "security-children",
    title: "Security and children",
    content: (
      <>
        <p>
          We use safeguards such as encrypted connections, access controls, isolated account
          sessions and rate limits. No internet service can guarantee absolute security. Please do
          not send passwords, access tokens or unnecessary sensitive information in support
          requests.
        </p>
        <p>
          DevFeed is a general developer resource and is not designed for children. If you believe a
          child has provided personal information inappropriately, contact us so we can investigate
          and take appropriate action.
        </p>
      </>
    ),
  },
  {
    id: "changes",
    title: "Policy updates",
    content: (
      <>
        <p>
          We will update this page when our practices change and revise the date above. We will
          provide additional notice or seek consent where required by applicable law.
        </p>
      </>
    ),
  },
  { id: "contact", title: "Contact", content: <LegalContact /> },
];

export default function PrivacyPage() {
  return (
    <LegalPage
      title="Privacy Policy"
      path="/legal/privacy"
      introduction={description}
      sections={sections}
    />
  );
}
