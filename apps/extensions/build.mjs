import { build } from "esbuild";
import { cp, mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../", import.meta.url)).replace(/\/$/, "");
const output = `${root}/apps/extensions/dist/chrome`;
await mkdir(output, { recursive: true });
await cp(`${root}/apps/extensions/chrome`, output, { recursive: true });
await cp(`${root}/packages/theme/assets/devfeed-mark.png`, `${output}/icon.png`);
await build({
  absWorkingDir: root,
  entryPoints: { newtab: "apps/extensions/src/entry.tsx" },
  bundle: true,
  outdir: output,
  target: "chrome120",
  jsx: "automatic",
  legalComments: "eof",
  define: { "process.env.NODE_ENV": '"production"' },
  alias: {
    "@": `${root}/apps/web/src`,
    "next/link": `${root}/apps/extensions/src/link.tsx`,
    "next/image": `${root}/apps/extensions/src/image.tsx`,
    "next/navigation": `${root}/apps/extensions/src/navigation.tsx`,
  },
  plugins: [
    {
      name: "bundled-brand-mark",
      setup(build) {
        build.onResolve({ filter: /^@devfeed\/theme\/assets\/devfeed-mark\.png$/ }, () => ({
          path: "icon.png",
          namespace: "brand",
        }));
        build.onLoad({ filter: /.*/, namespace: "brand" }, () => ({
          contents: 'export default { src: "icon.png" };',
          loader: "js",
        }));
      },
    },
  ],
  minify: true,
});
console.log(`Load unpacked: ${output}`);
