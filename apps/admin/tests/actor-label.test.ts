import { describe, expect, it } from "vitest";
import { actorLabel } from "@/lib/actor-label";

const identity = {
  subject: "389598389664220144",
  issuer: "https://identity.example",
  organization_id: "org",
};
const viewer = { ...identity, name: "Alex Morgan", email: "alex@example.com" };

describe("proposal attribution", () => {
  it("prefers the recorded name, then email", () => {
    expect(
      actorLabel({ ...identity, name: " Original name ", email: "old@example.com" }, viewer),
    ).toBe("Original name");
    expect(actorLabel({ ...identity, name: " ", email: "old@example.com" }, viewer)).toBe(
      "old@example.com",
    );
  });
  it("resolves an older proposal to the signed-in administrator", () => {
    expect(actorLabel(identity, viewer)).toBe("Alex Morgan");
    expect(actorLabel(identity, { ...viewer, name: null })).toBe("alex@example.com");
    expect(actorLabel(identity, { ...viewer, name: null, email: null })).toBe("You");
  });
  it.each(["subject", "issuer", "organization_id"] as const)(
    "does not assign the viewer's name when %s differs",
    (field) => {
      expect(actorLabel({ ...identity, [field]: "someone-else" }, viewer)).toBe("Administrator");
    },
  );
  it("never substitutes opaque IDs for missing profile names", () => {
    expect(actorLabel(identity)).toBe("Administrator");
    expect(actorLabel({ ...identity, name: identity.subject })).toBe("Administrator");
    expect(actorLabel({ subject: identity.subject }, viewer)).toBe("Administrator");
    expect(actorLabel(null, viewer)).toBe("—");
  });
  it("labels the known system actor", () => {
    expect(
      actorLabel({ subject: "ai-analysis", issuer: "devfeed", organization_id: "system" }),
    ).toBe("AI analysis");
  });
});
