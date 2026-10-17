import { describe, expect, it } from "vitest";
import { slugify, tagSlugify } from "@/lib/slug";

describe("slug generation", () => {
  it.each([
    ['"#React"', "react"],
    ["#C#", "c-sharp"],
    ["C++", "c-plus-plus"],
    [".NET 11", "net-11"],
    ["“ecosystem-security”", "ecosystem-security"],
  ])("preserves meaningful technical punctuation in tag %j", (name, slug) => {
    expect(tagSlugify(name)).toBe(slug);
  });

  it.each([
    ["React", "react"],
    ["  Generative AI & Infrastructure  ", "generative-ai-infrastructure"],
    ["Node.js / TypeScript", "node-js-typescript"],
    ["Développeur Café", "developpeur-cafe"],
    ["Developer’s Tools", "developers-tools"],
    ["GPT--5__Tutorials", "gpt-5-tutorials"],
    ["ＦａｓｔＡＰＩ ２", "fastapi-2"],
    ["日本語 React", "react"],
    ["日本語", ""],
    ["--- 🛠️ ---", ""],
    ["", ""],
  ])("normalizes %j to %j", (name, slug) => {
    expect(slugify(name)).toBe(slug);
    if (slug) expect(slug).toMatch(/^[a-z0-9]+(?:-[a-z0-9]+)*$/);
  });

  it("respects the API length limit without leaving a trailing hyphen", () => {
    expect(slugify("a".repeat(120))).toBe("a".repeat(100));
    expect(slugify(`${"a".repeat(99)} b`)).toBe("a".repeat(99));
    expect(slugify("React Native", 6)).toBe("react");
  });

  it("collapses long punctuation runs while retaining Unicode normalization", () => {
    const punctuation = "- /🛠️_".repeat(50_000);
    expect(slugify(`${punctuation}Développeur’s Café${punctuation}`)).toBe("developpeurs-cafe");
    expect(slugify(punctuation)).toBe("");
    expect(tagSlugify(`${punctuation}C++${punctuation}`)).toBe("c-plus-plus");
  });

  it("retains existing slice boundaries after separator normalization", () => {
    expect(slugify("A B", 2)).toBe("a");
    expect(slugify("React Native", 0)).toBe("");
    expect(slugify("React Native", -1)).toBe("react-nativ");
  });
});
