import {
  siDotnet,
  siKubernetes,
  siTypescript,
  siJavascript,
  siReact,
  siNextdotjs,
  siNodedotjs,
  siPython,
  siRust,
  siGo,
  siDocker,
  siPostgresql,
  siMysql,
  siRedis,
  siMongodb,
  siGit,
  siLinux,
  siCplusplus,
  siC,
  siPhp,
  siRuby,
  siRubyonrails,
  siSwift,
  siKotlin,
  siDart,
  siFlutter,
  siAngular,
  siVuedotjs,
  siSvelte,
  siTailwindcss,
  siHtml5,
  siCss,
  siTerraform,
  siGraphql,
  siElasticsearch,
  siSpring,
  siDjango,
  siFastapi,
  siTensorflow,
  siPytorch,
} from "simple-icons";

// Local, monochrome paths: no favicon requests or vendor colors outside our theme.
const icons: Record<string, string> = {
  dotnet: siDotnet.path,
  net: siDotnet.path,
  "net core": siDotnet.path,
  kubernetes: siKubernetes.path,
  k8s: siKubernetes.path,
  typescript: siTypescript.path,
  javascript: siJavascript.path,
  react: siReact.path,
  "react native": siReact.path,
  nextjs: siNextdotjs.path,
  nodejs: siNodedotjs.path,
  python: siPython.path,
  rust: siRust.path,
  go: siGo.path,
  golang: siGo.path,
  docker: siDocker.path,
  postgresql: siPostgresql.path,
  postgres: siPostgresql.path,
  mysql: siMysql.path,
  redis: siRedis.path,
  mongodb: siMongodb.path,
  git: siGit.path,
  linux: siLinux.path,
  "c++": siCplusplus.path,
  c: siC.path,
  php: siPhp.path,
  ruby: siRuby.path,
  "ruby on rails": siRubyonrails.path,
  rails: siRubyonrails.path,
  swift: siSwift.path,
  kotlin: siKotlin.path,
  dart: siDart.path,
  flutter: siFlutter.path,
  angular: siAngular.path,
  vue: siVuedotjs.path,
  vuejs: siVuedotjs.path,
  svelte: siSvelte.path,
  tailwindcss: siTailwindcss.path,
  "tailwind css": siTailwindcss.path,
  html: siHtml5.path,
  html5: siHtml5.path,
  css: siCss.path,
  terraform: siTerraform.path,
  graphql: siGraphql.path,
  elasticsearch: siElasticsearch.path,
  spring: siSpring.path,
  "spring boot": siSpring.path,
  django: siDjango.path,
  fastapi: siFastapi.path,
  tensorflow: siTensorflow.path,
  pytorch: siPytorch.path,
};

export function devCardTechnologyPath(name: string): string | null {
  const key = name.trim().toLowerCase().replace(/\./g, "");
  return icons[key] ?? null;
}

export function DevCardTechnologyIcon({ name }: { name: string }) {
  const path = devCardTechnologyPath(name);
  if (!path) return null;
  return (
    <svg
      x="14"
      y="14"
      width="36"
      height="36"
      aria-hidden="true"
      viewBox="0 0 24 24"
      data-technology-icon="brand"
    >
      <path fill="var(--secondary-foreground)" d={path} />
    </svg>
  );
}
