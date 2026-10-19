"use client";

import { useBoardMotion } from "./use-board-motion";

const articleAttributes = {
  id: "data-pipeline-article-id",
  stage: "data-pipeline-stage",
};

export function usePipelineMotion(layoutKey = "") {
  return useBoardMotion(layoutKey, articleAttributes);
}
