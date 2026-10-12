import {
  PartnerAccountPreferencesUpdateResponse,
  PartnerAudienceTopicsResponse,
  PartnerCreativePreviewResponse,
  PartnerDestinationPreviewResponse,
} from "./api/generated/validators";

export type { PartnerPreferencesInput } from "./api/generated/models";

export function parseAccount(value: unknown, expectedId: string) {
  const account = PartnerAccountPreferencesUpdateResponse.parse(value);
  if (account.id !== expectedId) throw new Error("Unexpected partner account");
  if (new Set(account.ad_formats).size !== account.ad_formats.length) {
    throw new Error("Duplicate advertising formats");
  }
  return account;
}

export function parseDestinationPreview(value: unknown) {
  return PartnerDestinationPreviewResponse.parse(value).product;
}

export function parseCreativePreview(value: unknown) {
  return PartnerCreativePreviewResponse.parse(value).product;
}

export function parseAudienceTopics(value: unknown) {
  return PartnerAudienceTopicsResponse.parse(value);
}
