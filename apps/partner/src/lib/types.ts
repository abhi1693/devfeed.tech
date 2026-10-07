export interface Identity {
  subject: string;
  name: string | null;
  roles: string[];
  csrf_token: string;
}
export interface Account {
  id: string;
  name: string;
  tier: string;
  benefits: string[];
  status: "active" | "paused";
}
export interface Totals {
  impressions: number;
  clicks: number;
  ctr: number | null;
  measured_days: number;
}
export interface Asset extends Totals {
  id: string;
  name: string;
  kind: "product" | "ad";
  status: "draft" | "active" | "paused" | "ended";
  product_id: string | null;
}
export interface Dashboard {
  account: Account;
  start: string;
  end: string;
  totals: Totals;
  last_updated_at: string | null;
  trend: { day: string; impressions: number; clicks: number }[];
  assets: Asset[];
  asset_total: number;
}
export interface AccountPage {
  items: Account[];
  total: number;
}
