import { connectorDefaults } from "@/lib/partner-connectors";
import type { ConnectorConfig } from "@/lib/api/generated/models";

const styles = [
  "border-cyan-600 bg-cyan-500/10 text-cyan-800 dark:text-cyan-200",
  "border-violet-600 bg-violet-500/10 text-violet-800 dark:text-violet-200",
  "border-emerald-600 bg-emerald-500/10 text-emerald-800 dark:text-emerald-200",
  "border-amber-600 bg-amber-500/10 text-amber-800 dark:text-amber-200",
  "border-blue-600 bg-blue-500/10 text-blue-800 dark:text-blue-200",
  "border-rose-600 bg-rose-500/10 text-rose-800 dark:text-rose-200",
  "border-lime-600 bg-lime-500/10 text-lime-800 dark:text-lime-200",
  "border-orange-600 bg-orange-500/10 text-orange-800 dark:text-orange-200",
  "border-fuchsia-600 bg-fuchsia-500/10 text-fuchsia-800 dark:text-fuchsia-200",
  "border-teal-600 bg-teal-500/10 text-teal-800 dark:text-teal-200",
];
export type ResponseMapping = {
  field: string;
  label: string;
  style: string;
  addresses: string[];
};
// Use segment arrays rather than joined paths: JSON keys may themselves contain dots.
export function responseMappings(config: ConnectorConfig, data: unknown): ResponseMapping[] {
  const at = (root: unknown, path: string): unknown => {
    let value = root;
    for (const segment of path ? path.split(".") : []) {
      if (Array.isArray(value) && /^\d+$/.test(segment)) value = value[Number(segment)];
      else if (value && typeof value === "object" && Object.hasOwn(value, segment))
        value = (value as Record<string, unknown>)[segment];
      else return undefined;
    }
    return value;
  };
  const valid = (value: unknown) => value !== undefined && value !== null && value !== "";
  const defaults = connectorDefaults();
  const fields = { ...defaults.fields, ...config.fields };
  const listPath = (config.items_paths ?? defaults.items_paths ?? ["items"]).find((path) =>
    valid(at(data, path)),
  );
  const list = listPath === undefined ? undefined : at(data, listPath);
  const products = Array.isArray(list) ? list : [];
  const base = listPath ? listPath.split(".") : [];
  const definitions: [string, string, string[], boolean][] = [
    [
      "items_paths",
      "Product list",
      listPath === undefined || !Array.isArray(list) ? [] : [listPath],
      false,
    ],
    ["fields.external_id", "Product ID", fields.external_id ?? [], true],
    ["fields.name", "Product name", fields.name ?? [], true],
    ["fields.product_url", "Website URL", fields.product_url ?? [], true],
    ["fields.listing_url", "Listing URL", fields.listing_url ?? [], true],
    ["fields.description", "Description", fields.description ?? [], true],
    ["fields.pricing", "Pricing", fields.pricing ?? [], true],
    [
      "pagination.next_path",
      "Next token",
      config.pagination?.next_path ? [config.pagination.next_path] : [],
      false,
    ],
    [
      "pagination.total_path",
      "Total count",
      config.pagination?.total_path ? [config.pagination.total_path] : [],
      false,
    ],
    [
      "pagination.has_more_path",
      "Has more",
      config.pagination?.has_more_path ? [config.pagination.has_more_path] : [],
      false,
    ],
  ];
  return definitions.map(([field, label, paths, perProduct], index) => {
    const addresses: string[] = [];
    for (const [position, root] of (perProduct ? products : [data]).entries()) {
      const path = paths.find((candidate) => valid(at(root, candidate)));
      if (path !== undefined)
        addresses.push(
          JSON.stringify([
            ...(perProduct ? [...base, String(position)] : []),
            ...(path ? path.split(".") : []),
          ]),
        );
    }
    return { field, label, style: styles[index], addresses };
  });
}
