// @vitest-environment jsdom
import { useState } from "react";
import Link from "next/link";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { DropdownMenuItem } from "@devfeed/ui/dropdown-menu";
import {
  BoardColumnsMenu,
  KanbanBoard,
  KanbanColumn,
  KanbanCard,
  BoardCardActions,
} from "@/components/organisms/kanban-board";
import { boardCardStatuses } from "@/lib/board-status";
import { useBoardColumns } from "@/lib/use-board-columns";

const stages = ["todo", "done"] as const;
type Stage = (typeof stages)[number];
const labels = { todo: "Awaiting inspection", done: "Inspected" };
const actOnReview = vi.fn();
function Reviews() {
  const columns = useBoardColumns("test-review-columns", stages);
  const [selected, onSelect] = useState<Stage>("todo");
  return (
    <>
      <BoardColumnsMenu stages={stages} labels={labels} columns={columns} label="Review columns" />
      <KanbanBoard
        label="Inspections"
        stageLabel="Review stage"
        stages={columns.visible}
        labels={labels}
        counts={{ todo: 1, done: 0 }}
        selected={selected}
        onSelect={onSelect}
        layoutKey={columns.collapsed.join(",")}
      >
        {columns.visible.map((stage) => (
          <KanbanColumn
            key={stage}
            id={`inspection-${stage}`}
            contentId={`inspection-${stage}-items`}
            label={labels[stage]}
            total={stage === "todo" ? 1 : 0}
            countLabel={`${stage === "todo" ? 1 : 0} reviews`}
            collapsed={columns.collapsed.includes(stage)}
            selected={selected === stage}
            onCollapse={() => columns.toggleCollapsed(stage)}
          >
            {stage === "todo" && (
              <KanbanCard
                status={boardCardStatuses.waiting}
                title={<Link href="/inspections/one">Inspect release</Link>}
                actions={
                  <BoardCardActions label="Inspection actions" disabled={false}>
                    <DropdownMenuItem onSelect={actOnReview}>Approve inspection</DropdownMenuItem>
                  </BoardCardActions>
                }
                data-board-item-id="one"
                data-board-stage={stage}
              >
                <p>Release team</p>
              </KanbanCard>
            )}
          </KanbanColumn>
        ))}
      </KanbanBoard>
    </>
  );
}
afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});
it("renders a third domain with accessible column controls, status and keyboard actions", async () => {
  vi.stubGlobal("innerWidth", 1440);
  const user = userEvent.setup();
  render(<Reviews />);
  expect(screen.getByRole("region", { name: "Inspections" })).toBeTruthy();
  expect(screen.getByRole("img", { name: "Waiting" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Inspect release", level: 3 })).toBeTruthy();
  fireEvent.doubleClick(screen.getByRole("heading", { name: labels.todo, level: 2 }));
  const expand = screen.getByRole("button", { name: `Expand ${labels.todo} column` });
  expect(expand.getAttribute("aria-expanded")).toBe("false");
  expect(expand.getAttribute("aria-controls")).toBe("inspection-todo-items");
  await user.click(expand);
  const actions = screen.getByRole("button", { name: "Inspection actions" });
  actions.focus();
  await user.keyboard("{Enter}");
  await user.keyboard("{Enter}");
  expect(actOnReview).toHaveBeenCalledTimes(1);
});
it("shares column visibility controls and always keeps a selectable column", async () => {
  const user = userEvent.setup();
  render(<Reviews />);
  await user.click(screen.getByRole("button", { name: "Columns" }));
  const menu = screen.getByLabelText("Review columns");
  await user.click(within(menu).getByRole("checkbox", { name: labels.todo }));
  expect(
    (within(menu).getByRole("checkbox", { name: labels.done }) as HTMLInputElement).disabled,
  ).toBe(true);
  expect(screen.queryByRole("heading", { name: labels.todo, level: 2 })).toBeNull();
  await user.click(within(menu).getByRole("button", { name: "Show all columns" }));
  expect(screen.getByRole("heading", { name: labels.todo, level: 2 })).toBeTruthy();
});
