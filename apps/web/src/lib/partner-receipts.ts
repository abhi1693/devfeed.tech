const receipt = /^v1\.[0-9a-f]{32}\.[0-9a-f]{32}\.([0-9]{1,12})\.[0-9a-f]{64}$/;
export const PARTNER_RECEIPT_TTL_MS = 60 * 60_000;

/** Mirror the server's one-hour RECEIPT_TTL; signature verification remains server-side. */
export function partnerReceiptExpiresAt(value: string): number {
  const match = receipt.exec(value);
  if (!match || match[0] !== value) return 0;
  return Number(match[1]) * 1000 + PARTNER_RECEIPT_TTL_MS;
}
