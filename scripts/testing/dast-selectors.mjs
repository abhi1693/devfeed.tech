export function pageContentSelector(path) {
  return path.startsWith("/articles/")
    ? "dialog.article-modal[open] #article-preview-title"
    : "main#main:visible:not(:has(.loading-skeleton))";
}
