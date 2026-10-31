import { pathToFileURL, fileURLToPath } from "node:url";
const root = fileURLToPath(new URL("../../../", import.meta.url)).replace(/\/$/, "");
const load = async (p) => import(pathToFileURL(root + p));
const [
  { default: React },
  { renderToStaticMarkup },
  { default: Markdown },
  { default: gfm },
  { createMarkdownParseCache },
] = await Promise.all([
  load("/node_modules/react/index.js"),
  load("/node_modules/react-dom/server.node.js"),
  load("/node_modules/react-markdown/index.js"),
  load("/node_modules/remark-gfm/index.js"),
  load("/packages/ui/src/markdown-cache.ts"),
]);
const source =
  '# Developer overview\n\n**Python** and [Kubernetes](https://kubernetes.io) support *concurrent* workloads.\n\n| System | Language |\n| --- | --- |\n| API | Python |\n\n- durable queues\n- safe deployments\n\n```python\nprint("development")\n```\n\n'.repeat(
    3,
  );
for (const mode of ["repeat", "unique"]) {
  const reports = {};
  for (const enabled of [false, true]) {
    const values = [];
    for (let round = 0; round < 3; round++) {
      const cache = createMarkdownParseCache();
      const options = { remarkPlugins: enabled ? [gfm, cache.plugin] : [gfm], skipHtml: true };
      for (let i = 0; i < 20; i++)
        renderToStaticMarkup(React.createElement(Markdown, options, source));
      cache.clear();
      const start = process.cpuUsage();
      const wall = performance.now();
      for (let i = 0; i < 250; i++)
        renderToStaticMarkup(
          React.createElement(
            Markdown,
            options,
            source + (mode === "unique" ? `\nArticle ${i}` : ""),
          ),
        );
      const cpu = process.cpuUsage(start);
      values.push({
        cpuMs: (cpu.user + cpu.system) / 1000,
        wallMs: performance.now() - wall,
        cache: cache.stats(),
      });
    }
    values.sort((a, b) => a.cpuMs - b.cpuMs);
    reports[enabled ? "cache" : "baseline"] = values[1];
  }
  console.log(JSON.stringify({ mode, characters: source.length, renders: 250, reports }));
}
