// @vitest-environment jsdom
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { JsonLd } from "@/components/json-ld";
import { articleStructuredData, siteStructuredData } from "@/lib/structured-data";
import { articleMetadata } from "@/lib/metadata";
import { article } from "./fixtures";

it.each(["script", "SCRIPT"])("keeps distinct stable graph identifiers with %s tags", (tag) => {
  const logo = { src: "/mark.png", width: 512, height: 512 };
  const html = renderToStaticMarkup(
    <>
      <JsonLd data={siteStructuredData(logo)} />
      <JsonLd data={articleStructuredData(article)} />
    </>,
  );
  const document = new DOMParser().parseFromString(
    html.replaceAll("<script", `<${tag}`).replaceAll("</script>", `</${tag}>`),
    "text/html",
  );
  const graphs = [...document.querySelectorAll('script[type="application/ld+json"]')].map(
    (script) => JSON.parse(script.textContent ?? "null"),
  );
  expect(graphs).toHaveLength(2);
  const entities = [
    ...graphs[0]["@graph"],
    graphs[1],
    graphs[1].mainEntity,
    graphs[1].breadcrumb,
    graphs[0]["@graph"][0].logo,
  ];
  const ids = entities.map((entity) => entity["@id"]);
  expect(new Set(ids).size).toBe(ids.length);
  expect(graphs[1].url).toBe(articleMetadata(article).alternates?.canonical);
  expect(graphs[1].mainEntity.publisher.name).not.toBe("DevFeed");
});
