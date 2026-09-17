// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SourceRelevanceAssessment } from "@/components/organisms/source-relevance-assessment";

vi.mock("@/components/molecules/date-time", () => ({
  DateTime: ({ value }: { value: string }) => <span>{value}</span>,
}));
afterEach(cleanup);
const assessment = {
  version: "source-relevance-v4",
  relevance: "relevant",
  confidence: 0.93,
  approval_supported: false,
  rejection_supported: false,
  reason: "Developer-focused engineering leadership clearly predominates.",
  sample: Array.from({ length: 10 }, (_, index) => ({
    index,
    title: `Article ${index}`,
    summary: `Summary ${index}`,
  })),
  entries: Array.from({ length: 10 }, (_, index) => ({
    index,
    relevance: index < 7 ? "relevant" : "uncertain",
    evidence: index < 7 ? `Supporting quote ${index}` : "",
  })),
};
describe("source relevance assessment", () => {
  it("explains source-level approval without applying the old article percentage", () => {
    render(
      <SourceRelevanceAssessment
        assessment={{ ...assessment, version: "source-relevance-v5", approval_supported: true }}
        approvalStatus="approved"
      />,
    );
    expect(screen.getByText("Evidence supports automatic approval")).toBeDefined();
    expect(screen.queryByText(/approval needs at least 8/)).toBeNull();
    expect(
      screen.getByText(/There is no article percentage requirement for approval/),
    ).toBeDefined();
    expect(screen.queryByText(/previous 80% approval rule/)).toBeNull();
  });
  it("limits evidence to five entries and resets pagination when filtering", () => {
    render(<SourceRelevanceAssessment assessment={assessment} approvalStatus="pending" />);
    const list = screen.getByRole("list", { name: "Sampled articles" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(5);
    expect(screen.queryByText("Article 5")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Article 5")).toBeDefined();
    expect(screen.queryByText("Article 0")).toBeNull();
    fireEvent.change(screen.getByRole("combobox", { name: "Filter article evidence" }), {
      target: { value: "uncertain" },
    });
    expect(within(list).getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getByText("1–3 of 3")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Next" })).toBeNull();
    fireEvent.change(screen.getByRole("combobox", { name: "Filter article evidence" }), {
      target: { value: "unrelated" },
    });
    expect(screen.getByText("No matching entries")).toBeDefined();
    expect(within(list).queryAllByRole("listitem")).toHaveLength(0);
  });
  it("identifies historical assessments made before the broader source policy", () => {
    render(
      <SourceRelevanceAssessment
        assessment={{ ...assessment, version: "source-relevance-v3" }}
        approvalStatus="approved"
      />,
    );
    expect(screen.getByText(/earlier developer-only scope/)).toBeDefined();
    expect(screen.getByText("Approved")).toBeDefined();
  });
  it("explains why high confidence and overall relevance did not approve 7 of 10 entries", () => {
    render(<SourceRelevanceAssessment assessment={assessment} approvalStatus="pending" />);
    expect(screen.getByText("Why this source is still pending")).toBeDefined();
    expect(
      screen.getByText("7 of 10 entries are in scope; approval needs at least 8 of 10 (80%)."),
    ).toBeDefined();
    expect(screen.getByText("93%")).toBeDefined();
    expect(screen.getByText(/Uncertain entries are not evidence for rejection/)).toBeDefined();
    expect(screen.getByText("Assessment details").closest("details")?.open).toBe(false);
  });
  it("pairs evidence by index even when model entries arrive in a different order", () => {
    render(
      <SourceRelevanceAssessment
        assessment={{ ...assessment, entries: [...assessment.entries].reverse() }}
        approvalStatus="pending"
      />,
    );
    const article = screen.getByText("Article 0").closest("details")!;
    expect(article.textContent).toContain("Supporting quote 0");
    expect(article.textContent).not.toContain("Supporting quote 6");
  });
  it("uses the stored decision instead of inferring approval from counts", () => {
    render(
      <SourceRelevanceAssessment
        assessment={{
          ...assessment,
          entries: assessment.entries.map((e) => ({ ...e, relevance: "relevant" })),
        }}
        approvalStatus="pending"
      />,
    );
    expect(screen.getByText("No automatic decision")).toBeDefined();
    expect(screen.getByText(/does not confirm enough validated evidence/)).toBeDefined();
  });
  it.each(["approval", "rejection"])(
    "separates supported %s from actual source status",
    (decision) => {
      render(
        <SourceRelevanceAssessment
          assessment={{ ...assessment, [`${decision}_supported`]: true }}
          approvalStatus="pending"
        />,
      );
      expect(screen.getByText(`Evidence supports automatic ${decision}`)).toBeDefined();
      expect(screen.getByText("Pending")).toBeDefined();
      expect(screen.queryByText("Why this source is still pending")).toBeNull();
    },
  );
  it("explains insufficient entries without displaying missing confidence as zero", () => {
    render(
      <SourceRelevanceAssessment
        assessment={{ version: "source-relevance-v4", sample: [], relevance: "uncertain" }}
        approvalStatus="pending"
      />,
    );
    expect(screen.getByText("Only 0 usable entries; at least 3 are required.")).toBeDefined();
    expect(screen.getByText("Not reported")).toBeDefined();
  });
  it("explains the rejection threshold and preserves below-threshold confidence precision", () => {
    render(
      <SourceRelevanceAssessment
        assessment={{
          ...assessment,
          relevance: "unrelated",
          confidence: 0.899,
          entries: assessment.entries.map((e) => ({
            ...e,
            relevance: e.relevance === "relevant" ? "unrelated" : "uncertain",
          })),
        }}
        approvalStatus="pending"
      />,
    );
    expect(screen.getByText("Confidence is 89.9%; at least 90% is required.")).toBeDefined();
    expect(screen.getByText(/rejection needs at least 8 of 10/)).toBeDefined();
  });
  it.each([null, {}, "bad data"])("handles an absent assessment: %j", (value) => {
    render(<SourceRelevanceAssessment assessment={value} approvalStatus="pending" />);
    expect(screen.getByText("No assessment available")).toBeDefined();
  });
  it("does not apply known thresholds to a future policy or describe an approved source as pending", () => {
    render(
      <SourceRelevanceAssessment
        assessment={{ ...assessment, version: "future" }}
        approvalStatus="approved"
      />,
    );
    expect(screen.queryByText(/approval needs at least/)).toBeNull();
    expect(screen.getByText("Why this assessment could not decide")).toBeDefined();
  });
});
