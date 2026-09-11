"use client";
import { ArticleModal } from "@/components/article-modal";
export default function PreviewError({ reset }: { reset: () => void }) {
  return (
    <ArticleModal>
      <h2>Couldn’t load this article</h2>
      <p>Please try again shortly.</p>
      <button className="button" onClick={reset}>
        Try again
      </button>
    </ArticleModal>
  );
}
