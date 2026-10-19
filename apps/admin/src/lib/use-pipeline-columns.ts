"use client";

import { pipelineStages } from "./article-pipeline";
import { useBoardColumns } from "./use-board-columns";

export const pipelineColumnsStorageKey = "devfeed:admin-article-pipeline-columns";

export function usePipelineColumns() {
  return useBoardColumns(pipelineColumnsStorageKey, pipelineStages);
}
