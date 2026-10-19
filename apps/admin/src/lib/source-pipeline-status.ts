import { boardCardStatuses } from "./board-status";

const { success, ...statuses } = boardCardStatuses;

export const sourceCardStatuses = {
  ...statuses,
  active: { ...success, label: "Active" },
  paused: { ...statuses.waiting, label: "Paused" },
};
