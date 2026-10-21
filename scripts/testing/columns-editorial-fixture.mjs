import { columnsSharedArticle } from "./columns-feed-fixture.mjs";
import { columnsCatalog } from "./columns-workspace.mjs";

// Public headline snapshots with deliberately varied synthetic metadata for visual QA.
// These fixtures never change publication, delivery policy, or a real account.
const editorial = [
  {
    title:
      "GitHub Copilot's Project HydraFusion Promises Frontier Level Performance Through Multi-Model Routing",
    description:
      "The article describes HydraFusion's capability-based routing, isolated critique workflow, validation safeguards, and offline benchmark results. On TerminalBench 2.1, the reported result was a 4.9 percentage-point quality improvement alongside a 67% estimated cost reduction.",
    source: "InfoQ",
    topic: "Software architecture and distributed systems",
    tags: ["engineering", "developer-productivity"],
    date: "2026-10-22T06:20:00Z",
  },
  {
    title: "Launching post-quantum safe passkeys",
    description: "Passkeys are getting ready for a post-quantum world.",
    source: "Blog on 1Password Blog",
    topic: "Security",
    tags: ["engineering", "developer-productivity"],
    date: "2026-10-21T13:45:00Z",
  },
  {
    title: "How we deployed post-quantum TLS without any code changes",
    description:
      "1Password describes deploying hybrid post-quantum TLS for first-party clients and controlled endpoints through AWS load-balancer policy and Rustls cryptographic-provider configuration, without application-code changes.",
    source: "Blog on 1Password Blog",
    topic: "Open source",
    tags: ["engineering", "developer-productivity"],
    date: "2026-10-18T15:00:00Z",
  },
  {
    title:
      "September 2026 Patch Tuesday: Two Exploited Zero-Days and 113 Critical Vulnerabilities Among 972 CVEs",
    description:
      "A security update analysis covering the scale and severity of Microsoft’s September 2026 patches, leading exploitation categories, and the ShieldCrash proof-of-concept targeting Microsoft Defender.",
    source: "The Pragmatic Engineer — Software Architecture and Engineering Leadership",
    topic: "Platform engineering",
    tags: ["engineering", "developer-productivity"],
    date: "2026-09-14T12:00:00Z",
  },
  {
    title: "Grafana 13.2 release: easier ways to query and explore your data",
    description: "",
    source: "Grafana Labs blog on Grafana Labs",
    topic: "Software architecture and distributed systems",
    tags: ["engineering", "developer-productivity"],
    date: "2026-08-03T04:30:00Z",
  },
  {
    title: "SaaS access governance: Does access end when employees leave?",
    description:
      "Why disabling IdP or SSO access may leave SaaS access behind, and how lifecycle governance, offboarding verification, and integration-specific workflows can help manage it.",
    source: "AWS Architecture Blog — Cloud Operations and Infrastructure Engineering",
    topic: "Security",
    tags: ["engineering", "developer-productivity"],
    date: "2026-10-22T06:20:00Z",
  },
  {
    title: "How to monitor Cypress tests with Grafana Cloud",
    description:
      "The article explains a Cypress test-monitoring pipeline that retains short-lived test-run metrics to identify failures, slowdowns, and broader trends.",
    source: "Grafana Labs blog on Grafana Labs",
    topic: "Observability, infrastructure automation and production reliability",
    tags: ["engineering", "developer-productivity"],
    date: "2026-10-21T13:45:00Z",
  },
  {
    title: "The AI-native SDLC won’t be one process",
    description: "How to choose controls that match the risk and scale of each change.",
    source: "The New Stack",
    topic: "Platform engineering",
    tags: ["engineering", "developer-productivity"],
    date: "2026-10-18T15:00:00Z",
  },
  {
    title: "“Same mission, bigger stage”: OpenAI hires Git AI founders to help Codex prove its ROI",
    description:
      "Package migration notes: @platform-engineering/production-observability-and-distributed-tracing-configuration-without-downtime.",
    source: "The New Stack",
    topic: "Software architecture and distributed systems",
    tags: ["engineering", "developer-productivity"],
    date: "2026-09-14T12:00:00Z",
  },
  {
    title: "1Password named a leader in the GigaOm Radar for Enterprise Password Management",
    description:
      "1Password’s account of its Leader and “Disruptive Pioneer” recognition in GigaOm’s 2026 Enterprise Password Management Radar, highlighting enterprise password, developer-secret, passwordless, and AI-agent credential-management capabilities.",
    source: "Blog on 1Password Blog",
    topic: "",
    tags: [
      "platformengineeringanddistributedinfrastructureobservability",
      "release-management",
      "security",
    ],
    date: "2026-08-03T04:30:00Z",
  },
  {
    title:
      "Designing reliable event pipelines: retries, idempotency and graceful recovery in production",
    description:
      "A practical engineering guide to handling partial failures without losing events or duplicating downstream work.",
    source: "Blog on 1Password Blog",
    topic: "Open source",
    tags: ["engineering", "developer-productivity"],
    date: "2026-10-22T06:20:00Z",
    sponsored: true,
  },
  {
    title: "Niantic Spatial CEO on bringing AI into the physical world",
    description: "",
    source: "Blog on 1Password Blog",
    topic: "Platform engineering",
    tags: ["engineering", "developer-productivity"],
    date: "2026-10-21T13:45:00Z",
  },
];

export const scanningKinds = ["recommended", "source", "topic", "latest", "tag"];
export const scanningCatalog = {
  sources: [
    {
      ...columnsCatalog.sources[0],
      name: "The Pragmatic Engineer — Architecture and Engineering Leadership",
    },
  ],
  topics: [
    { ...columnsCatalog.topics[0], name: "Distributed systems and application architecture" },
    columnsCatalog.topics[1],
  ],
  tags: [{ ...columnsCatalog.tags[0], name: "Platform engineering and developer productivity" }],
};

export function scanningArticles(kind) {
  const offset = scanningKinds.indexOf(kind) * 3;
  return editorial.map((_, index) => {
    const item = editorial[(offset + index) % editorial.length];
    const suffix = String(scanningKinds.indexOf(kind) * 100 + index + 1).padStart(12, "0");
    return {
      ...columnsSharedArticle,
      id: `a1111111-1111-4111-8111-${suffix}`,
      slug: `scanning-${kind}-${index}`,
      title: item.title,
      summary: item.description,
      ai_description: null,
      ai_summary: index % 3 === 0 ? item.description : null,
      canonical_url: `https://publisher.example.test/articles/scanning-${kind}-${index}`,
      published_at: index === 7 ? null : item.date,
      feed_at: item.date,
      sources: [{ ...columnsCatalog.sources[0], name: item.source }],
      publisher: null,
      topics: item.topic ? [{ ...columnsCatalog.topics[0], name: item.topic }] : [],
      tags: item.tags,
      sponsor_names: item.sponsored ? ["Developer Tools Research Laboratory"] : undefined,
    };
  });
}
