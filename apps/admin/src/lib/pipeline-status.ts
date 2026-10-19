import { boardCardStatuses } from "./board-status";

const { success, ...statuses } = boardCardStatuses;

export const pipelineCardStatuses = { ...statuses, published: { ...success, label: "Published" } };
