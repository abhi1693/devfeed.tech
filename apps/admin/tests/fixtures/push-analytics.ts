import type { PushAnalytics, PushAnalyticsMetrics } from "@/lib/api/generated/models";

export const emptyPushMetrics: PushAnalyticsMetrics = {
  published_events: 0,
  recipient_accounts: 0,
  browser_deliveries: 0,
  accepted: 0,
  displayed: 0,
  clicked: 0,
  opened: 0,
  failed: 0,
  skipped: 0,
  queued: 0,
  running: 0,
  retries: 0,
  click_rate: 0,
};

export function pushAnalyticsFixture(days = 30): PushAnalytics {
  const now = new Date();
  const daily = Array.from({ length: days }, (_, index) => {
    const active = index >= days - 7;
    return {
      ...emptyPushMetrics,
      date: new Date(
        Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - days + index + 1),
      )
        .toISOString()
        .slice(0, 10),
      ...(active
        ? {
            published_events: 1,
            recipient_accounts: 1,
            browser_deliveries: 4,
            accepted: 3,
            displayed: 2,
            clicked: 1,
            opened: index >= days - 5 ? 1 : 0,
            failed: index >= days - 3 ? 1 : 0,
            skipped: index === days - 4 ? 1 : 0,
            queued: index === days - 5 || index === days - 6 ? 1 : 0,
            running: index === days - 7 ? 1 : 0,
            retries: index >= days - 3 ? 2 : 0,
            click_rate: 50,
          }
        : {}),
    };
  });
  const totals = {
    published_events: 7,
    recipient_accounts: 7,
    browser_deliveries: 28,
    accepted: 21,
    displayed: 14,
    clicked: 7,
    opened: 5,
    failed: 3,
    skipped: 1,
    queued: 2,
    running: 1,
    retries: 6,
    click_rate: 50,
  };
  return {
    generated_at: now.toISOString(),
    enabled: true,
    days,
    enabled_accounts: 9,
    enabled_subscriptions: 11,
    totals,
    daily,
    by_kind: [{ kind: "daily_must_read", ...totals }],
  };
}

export function emptyPushAnalyticsFixture(days = 30): PushAnalytics {
  const data = pushAnalyticsFixture(days);
  return {
    ...data,
    enabled_accounts: 0,
    enabled_subscriptions: 0,
    totals: emptyPushMetrics,
    daily: data.daily.map((row) => ({ ...emptyPushMetrics, date: row.date })),
    by_kind: [],
  };
}
