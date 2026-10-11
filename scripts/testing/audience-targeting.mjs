import assert from "node:assert/strict";

export const emptyAudience = {
  experience_levels: [],
  language_topic_ids: [],
  tool_topic_ids: [],
};
export const audienceLanguages = Array.from({ length: 60 }, (_, index) => ({
  id: `77777777-7777-4777-8777-${String(index).padStart(12, "0")}`,
  name: index === 58 ? "Python" : index === 59 ? "TypeScript" : `Language ${index + 1}`,
  slug: `language-${index + 1}`,
  kind: "language",
  status: "active",
}));
export const audienceTools = [
  {
    id: "88888888-8888-4888-8888-000000000001",
    name: "Docker",
    slug: "docker",
    kind: "tool",
    status: "active",
  },
  {
    id: "88888888-8888-4888-8888-000000000002",
    name: "Kubernetes",
    slug: "kubernetes",
    kind: "platform",
    status: "active",
  },
];
export const selectedAudience = {
  experience_levels: ["senior"],
  language_topic_ids: [audienceLanguages[59].id, audienceLanguages[58].id],
  tool_topic_ids: [audienceTools[1].id],
};

export function audienceTopicOptions(params) {
  const dimension = params.get("dimension");
  assert.ok(["languages", "tools"].includes(dimension));
  const source = dimension === "languages" ? audienceLanguages : audienceTools;
  const matching = source.filter((topic) =>
    params.has("topic_id")
      ? topic.id === params.get("topic_id")
      : topic.name.toLowerCase().includes((params.get("q") ?? "").toLowerCase()),
  );
  const limit = Number(params.get("limit") ?? 25);
  const offset = Number(params.get("offset") ?? 0);
  assert.ok(limit > 0 && limit <= 100, "Audience lookup requests stay bounded");
  return { items: matching.slice(offset, offset + limit), total: matching.length };
}

/** Select values beyond the first catalog page, proving server search and pagination. */
export async function selectAudience(page, scope, requests) {
  await scope.getByLabel("Senior", { exact: true }).check();
  await scope.getByRole("combobox", { name: "Programming languages", exact: true }).click();
  await page.getByRole("option", { name: /^Language 1 language$/ }).waitFor();
  assert.equal(await page.getByRole("option", { name: /^TypeScript\b/ }).count(), 0);
  await page.getByRole("button", { name: "Next programming languages", exact: true }).click();
  await page.getByRole("option", { name: /^Language 26 language$/ }).waitFor();
  assert.ok(requests.some((params) => params.dimension === "languages" && params.offset === "25"));
  await page.getByPlaceholder("Search programming languages…", { exact: true }).fill("TypeScript");
  await page.getByRole("option", { name: /^TypeScript\b/ }).click();
  await scope.getByRole("combobox", { name: "Programming languages", exact: true }).click();
  await page.getByPlaceholder("Search programming languages…", { exact: true }).fill("Python");
  await page.getByRole("option", { name: /^Python\b/ }).click();
  await scope.getByRole("combobox", { name: "Developer tools", exact: true }).click();
  await page.getByPlaceholder("Search developer tools…", { exact: true }).fill("Kubernetes");
  await page.getByRole("option", { name: /^Kubernetes\b/ }).click();
  await checkAudienceSelection(scope);
  for (const [dimension, q] of [
    ["languages", "TypeScript"],
    ["languages", "Python"],
    ["tools", "Kubernetes"],
  ])
    assert.ok(
      requests.some(
        (params) => params.dimension === dimension && params.q === q && params.offset === "0",
      ),
      `${dimension} search finds ${q} independently of downloaded pages`,
    );
}

export async function checkAudienceSelection(scope) {
  assert.equal(await scope.getByLabel("Senior", { exact: true }).isChecked(), true);
  const languages = scope.getByRole("list", {
    name: "Selected programming languages",
    exact: true,
  });
  await languages.getByText("TypeScript", { exact: true }).waitFor();
  await languages.getByText("Python", { exact: true }).waitFor();
  await scope
    .getByRole("list", { name: "Selected developer tools", exact: true })
    .getByText("Kubernetes", { exact: true })
    .waitFor();
}
