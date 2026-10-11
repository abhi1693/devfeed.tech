import type { PartnerPlacement } from "@/lib/partner-placements";

export const placement: PartnerPlacement = {
  product_id: "11111111-1111-4111-8111-111111111111",
  asset_id: "22222222-2222-4222-8222-222222222222",
  campaign_id: null,
  name: "TypeScript validator",
  description: "Validate application inputs.",
  url: "https://partner.example/validator",
  logo_url: "https://assets.partner.example/validator.png",
  image_url: null,
  image_variants: [],
  partner_name: "Example partner",
  sponsored: false,
  reason: "Supports the TypeScript validation task in this article.",
  receipt: "signed-delivery-receipt",
};
export const sponsoredPlacement: PartnerPlacement = {
  ...placement,
  product_id: "33333333-3333-4333-8333-333333333333",
  asset_id: "44444444-4444-4444-8444-444444444444",
  campaign_id: "55555555-5555-4555-8555-555555555555",
  sponsored: true,
  name: "Sponsored validator",
  receipt: "sponsored-delivery-receipt",
};
