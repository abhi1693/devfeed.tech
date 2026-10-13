import type { AriaAttributes, ComponentType, ReactNode } from "react";

export type ConnectorPaginationMode = "none" | "cursor" | "page" | "offset" | "next_url";
export type ConnectorAuthMode = "none" | "bearer" | "api_key";

/** Structural editing contract; service-generated API types remain the wire contract. */
export type ConnectorDefinition = {
  base_url: string;
  list_path?: string;
  detail_path?: string | null;
  items_paths?: string[];
  detail_root?: string;
  listing_url_template?: string | null;
  fields?: Partial<
    Record<
      "external_id" | "name" | "product_url" | "listing_url" | "description" | "pricing",
      string[]
    >
  >;
  pagination?: {
    mode?: ConnectorPaginationMode;
    parameter?: string | null;
    size_parameter?: string | null;
    page_size?: number;
    start?: number;
    next_path?: string;
    total_path?: string | null;
    has_more_path?: string | null;
  };
  auth?: {
    mode?: ConnectorAuthMode;
    secret_ref?: string;
    header?: string;
  };
  filters?: { path: string; values: string[]; include_missing?: boolean }[];
  parameters?: Record<string, unknown>;
  platform_hosts?: string[];
  attribution?: string;
  requests_per_minute?: number;
  timeout_seconds?: number;
  max_pages?: number;
  max_response_bytes?: number;
};

export type ConnectorFieldControl = Pick<AriaAttributes, "aria-describedby" | "aria-invalid"> & {
  id: string;
  name?: string;
  required: boolean;
  disabled: boolean;
};

export type ConnectorFieldProps = {
  label: string;
  required?: boolean;
  disabled?: boolean;
  tooltip?: string;
  error?: string;
  children: (control: ConnectorFieldControl) => ReactNode;
};

export type ConnectorFieldRenderer = ComponentType<ConnectorFieldProps>;
