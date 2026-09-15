export function onboardingTopics(topic) {
  return [
    topic,
    ...[
      "Python",
      "React",
      "Docker",
      "Kubernetes",
      "Rust",
      "Go",
      "PostgreSQL",
      "Local AI",
      "Linux",
      "Web Performance",
      "Open Source",
    ].map((name, index) => ({
      ...topic,
      id: `onboarding-${index}`,
      name,
      slug: name.toLowerCase().replaceAll(" ", "-"),
      logo_url: null,
      description: `Articles about ${name}.`,
    })),
  ];
}

export const onboardingSources = [
  {
    id: "11111111-1111-4111-8111-111111111111",
    slug: "engineering-journal",
    name: "Engineering Journal",
    website_url: "https://example.com",
    logo_url: null,
    description: "Practical engineering articles.",
  },
  {
    id: "33333333-3333-4333-8333-333333333333",
    slug: "developer-notes",
    name: "Developer Notes",
    website_url: "https://notes.example.test",
    logo_url: null,
    description: "Tools and ideas for developers.",
  },
];
