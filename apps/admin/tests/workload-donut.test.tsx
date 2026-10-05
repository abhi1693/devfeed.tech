// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { WorkloadDonut } from "@/components/organisms/workload-donut";

afterEach(cleanup);

it("associates workload terms with their counts in valid definition lists", () => {
  render(
    <WorkloadDonut
      rows={[{ kind: "feed", label: "Feed", href: "/jobs", queued: 5, running: 2 }]}
      queuedDelta={0}
      runningDelta={0}
      compared={false}
    />,
  );
  for (const [label, count] of [
    ["running now", "2"],
    ["queued", "5"],
  ]) {
    const term = screen.getByText(label, { selector: "dt" });
    expect(term.previousElementSibling).toBeNull();
    expect(term.nextElementSibling?.tagName).toBe("DD");
    expect(term.nextElementSibling?.textContent).toBe(count);
  }
});
