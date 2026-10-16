import {
  matchReaderDestinationPath,
  readerDestinationContract,
  validReaderParameter,
  type ReaderDestinationDefinition,
  type ReaderDestinationRecord,
  type ReaderSettingsSection,
} from "@devfeed/http/reader-destinations";
import { contentTypeFromRoute, type ContentType } from "./feed-query";
import { readerFeeds, type FeedDescriptor } from "./reader-feed";

export type CatalogKind = "topics" | "sources";
type CollectionKind = CatalogKind | "tags";
export type SettingsPage = ReaderSettingsSection;

export type ReaderRoute =
  | { type: "local"; page: "catalog"; catalog: CatalogKind }
  | { type: "local"; page: "settings"; settings: SettingsPage }
  | {
      type: "local";
      page: "source-suggestion" | "mcp" | "leaderboard" | "dev-card" | "mcp-authorize";
    }
  | { type: "article"; slug: string }
  | { type: "profile"; username: string }
  | {
      type: "reader";
      feed: FeedDescriptor;
      detail?: { kind: CatalogKind; slug: string; contentType?: ContentType };
      contentType?: ContentType;
    };

export type ReaderRouteLabels = { telemetry: string; screen: string };

function routeIdentity(route: ReaderRoute): { id: string; parameters: Record<string, string> } {
  switch (route.type) {
    case "article":
      return { id: "article", parameters: {} };
    case "profile":
      return { id: "profile", parameters: {} };
    case "local":
      if (route.page === "settings")
        return { id: "settings", parameters: { section: route.settings } };
      return { id: route.page === "catalog" ? route.catalog : route.page, parameters: {} };
    case "reader":
      if (route.detail)
        return {
          id: `${route.detail.kind}-${route.detail.contentType ? "content" : "detail"}`,
          parameters: {},
        };
      if (route.contentType)
        return { id: "content", parameters: { contentType: route.contentType } };
      return { id: route.feed.mode === "personal" ? "home" : route.feed.mode, parameters: {} };
  }
}

function definitionLabels(
  definition: ReaderDestinationDefinition,
  parameters: Record<string, string>,
): ReaderRouteLabels | null {
  if (!definition.labels) return null;
  const project = (template: string) =>
    template.replace(/:([a-zA-Z]+)/g, (placeholder, name: string) => {
      const segment = definition.segments.find(
        (part) => typeof part !== "string" && part.name === name,
      );
      if (!segment || typeof segment === "string") return placeholder;
      const approved =
        readerDestinationContract.parameters[segment.type].values?.[parameters[name]];
      return approved ?? placeholder;
    });
  return {
    telemetry: project(definition.labels.telemetry),
    screen: project(definition.labels.screen),
  };
}

/** Project only approved semantic fields; identifiers never enter either label. */
export function readerRouteLabels(route: ReaderRoute | null): ReaderRouteLabels | null {
  if (!route) return null;
  const { id, parameters } = routeIdentity(route);
  const definition = readerDestinationContract.routes.find((route) => route.id === id);
  return definition ? definitionLabels(definition, parameters) : null;
}

type ReaderTelemetryDefinition = {
  route: string;
  label: string;
  parameters?: Record<string, readonly string[]>;
};

/** Build-time metadata comes from the same destinations that navigation resolves. */
export const readerTelemetryDefinitions: readonly ReaderTelemetryDefinition[] =
  readerDestinationContract.routes.flatMap((definition) => {
    if (!definition.navigation || !definition.labels) return [];
    let variants: Record<string, string>[] = [{}];
    const routeParameters: Record<string, readonly string[]> = {};
    for (const segment of definition.segments) {
      if (typeof segment === "string") continue;
      const values = readerDestinationContract.parameters[segment.type].values;
      if (!values) continue;
      if (segment.static || definition.labels.telemetry.includes(`:${segment.name}`)) {
        variants = variants.flatMap((parameters) =>
          Object.keys(values).map((value) => ({ ...parameters, [segment.name]: value })),
        );
      } else routeParameters[segment.name] = Object.values(values);
    }
    const result = variants.map((parameters) => ({
      route: `/${definition.segments.map((segment) => (typeof segment === "string" ? segment : segment.static ? readerDestinationContract.parameters[segment.type].values![parameters[segment.name]] : `[${segment.name}]`)).join("/")}`,
      label: definitionLabels(definition, parameters)!.telemetry,
      ...(Object.keys(routeParameters).length ? { parameters: routeParameters } : {}),
      ...(!Object.keys(routeParameters).length &&
      Object.keys(parameters).length &&
      definition.segments.some((segment) => typeof segment !== "string" && !segment.static)
        ? {
            parameters: Object.fromEntries(
              Object.entries(parameters).map(([name, value]) => {
                const segment = definition.segments.find(
                  (segment) => typeof segment !== "string" && segment.name === name,
                )!;
                return [
                  name,
                  [
                    readerDestinationContract.parameters[(segment as { type: string }).type]
                      .values![value],
                  ],
                ];
              }),
            ),
          }
        : {}),
    }));
    return [
      ...result,
      ...(definition.aliases ?? []).map((alias) => ({
        route: `/${alias.segments.join("/")}`,
        label: definitionLabels(definition, alias.parameters)!.telemetry,
      })),
    ];
  });

/** The relay accepts this finite vocabulary, never a client-supplied raw path. */
export const readerScreenNames = [
  ...new Set(
    readerDestinationContract.routes.flatMap((definition) => {
      if (!definition.embedded || !definition.labels) return [];
      let variants: Record<string, string>[] = [{}];
      for (const segment of definition.segments) {
        if (typeof segment === "string") continue;
        const values = readerDestinationContract.parameters[segment.type].values;
        if (values && definition.labels.screen.includes(`:${segment.name}`))
          variants = variants.flatMap((parameters) =>
            Object.keys(values).map((value) => ({ ...parameters, [segment.name]: value })),
          );
      }
      return variants.map((parameters) => definitionLabels(definition, parameters)!.screen);
    }),
  ),
];

export function isArticleSlug(slug: string): boolean {
  return validReaderParameter("articleSlug", slug);
}

/** Accept decoded collection parameters, preserving technology tags such as c++ and c#. */
export function collectionRoute<Kind extends CollectionKind>(
  kind: Kind,
  slug: string,
  suffix?: string,
): { kind: Kind; slug: string; contentType?: ContentType } | null {
  const type = kind === "tags" ? "tag" : kind === "topics" ? "topicSlug" : "sourceSlug";
  if (!validReaderParameter(type, slug)) return null;
  if (suffix === undefined) return { kind, slug };
  const contentType = contentTypeFromRoute(suffix);
  return contentType ? { kind, slug, contentType } : null;
}

function navigationRoute(
  definition: ReaderDestinationDefinition,
  destination: ReaderDestinationRecord,
): ReaderRoute | null {
  const navigation = definition.navigation;
  const parameters = destination.parameters;
  if (!navigation || !definition.embedded) return null;
  switch (navigation.type) {
    case "article":
      return { type: "article", slug: parameters[navigation.slugParameter] };
    case "profile":
      return { type: "profile", username: parameters[navigation.usernameParameter] };
    case "local":
      if (navigation.page === "catalog")
        return { type: "local", page: "catalog", catalog: navigation.catalog };
      if (navigation.page === "settings")
        return {
          type: "local",
          page: "settings",
          settings: parameters[navigation.settingsParameter] as SettingsPage,
        };
      return {
        type: "local",
        page: navigation.page,
      };
    case "reader": {
      const contentType = navigation.contentTypeParameter
        ? (parameters[navigation.contentTypeParameter] as ContentType)
        : undefined;
      return {
        type: "reader",
        feed: readerFeeds[navigation.feed],
        ...(navigation.collection && navigation.collection !== "tags"
          ? {
              detail: {
                kind: navigation.collection,
                slug: parameters[navigation.slugParameter],
                ...(contentType ? { contentType } : {}),
              },
            }
          : contentType
            ? { contentType }
            : {}),
      };
    }
  }
}

/** Resolve only destinations owned by the embedded reader; other pages stay on the website. */
export function resolveReaderRoute(pathname: string): ReaderRoute | null {
  const matched = matchReaderDestinationPath(pathname);
  return matched ? navigationRoute(matched.definition, matched.destination) : null;
}
