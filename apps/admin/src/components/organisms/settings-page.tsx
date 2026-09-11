"use client";

import { useState } from "react";
import { timezoneOptions } from "@devfeed/ui/date-format";
import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import { Field } from "@/components/molecules/field";
import { LogoUrlField } from "@/components/molecules/logo-url-field";
import { PageTitle } from "@/components/molecules/page-title";
import { Select, type SelectOption } from "@/components/molecules/select";
import { SettingsForm, SettingToggle } from "@/components/molecules/settings-form";
import { useAdmin } from "@/components/molecules/admin-session";
import { defaultSettings, formatDate, settingsSections, type Settings, type SettingsSection } from "@/lib/settings";
import { useSettings } from "@/lib/use-settings";
import { notify, notifyFailure } from "@/lib/notifications";
import { NotificationSettingsForm } from "./notification-settings";

const options = (items: [string, string][]): SelectOption[] => items.map(([value, label]) => ({ value, label }));
function Choice({ label, value, onChange, items, search = false }: { label: string; value: string | number; onChange: (value: string) => void; items: SelectOption[]; search?: boolean }) {
  return <Field orientation="horizontal" label={label}>{control => <Select {...control} label={label} required value={String(value)} onChange={onChange} options={items} search={search ? {} : undefined} />}</Field>;
}
export function SettingsPage({ section }: { section: SettingsSection }) {
  const { settings, save } = useSettings(); const admin = useAdmin();
  const meta = settingsSections.find(item => item.id === section)!;
  return <section aria-label={meta.label} className="min-w-0 space-y-5">
    <PageTitle title={`${meta.label} settings`} />
    <p className="text-sm text-muted-foreground">{meta.description}</p>
    <div className="min-w-0">
      {section === "notifications" ? <NotificationSettingsForm /> : section === "profile" ? <SettingsForm key={section} initial={settings.profile} defaults={defaultSettings.profile} onSave={value => save("profile", value)}>{(value, change) => <>
        <Field orientation="horizontal" label="Display name" subtext="Used in your account menu. Your sign-in identity and audit records stay managed by your organization.">{control => <Input {...control} maxLength={100} placeholder={admin.name || "Your name"} value={value.display_name ?? ""} onChange={event => change({ ...value, display_name: event.target.value || null })} />}</Field>
        <Field orientation="horizontal" label="Avatar URL" subtext="Use a public image URL.">{control => <LogoUrlField {...control} value={value.avatar_url ?? ""} onChange={event => change({ ...value, avatar_url: event.target.value || null })} />}</Field>
        <div className="space-y-3 border-t pt-5"><h2 className="text-sm font-semibold">Managed by your organization</h2><dl className="grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-[12rem_minmax(0,1fr)]">{[["Email", admin.email || "Not provided"], ["Organization", admin.organization_id || "Not provided"], ["Roles", admin.roles.join(", ")]].map(([label, value]) => <div key={label} className="contents"><dt className="text-muted-foreground">{label}</dt><dd className="break-words">{value}</dd></div>)}</dl></div>
      </>}</SettingsForm> : section === "appearance" ? <AppearanceForm /> : <DefaultsForm />}
    </div>
  </section>;
}
function AppearanceForm() {
  const { settings, save } = useSettings();
  const [zones] = useState(() => timezoneOptions(settings.appearance.timezone));
  return <SettingsForm initial={settings.appearance} defaults={defaultSettings.appearance} onSave={value => save("appearance", value)}>{(value, change) => {
    const set = <K extends keyof Settings["appearance"]>(key: K, next: Settings["appearance"][K]) => change({ ...value, [key]: next });
    return <>
      <div className="space-y-5">
        <Choice label="Theme" value={value.theme} onChange={next => set("theme", next as typeof value.theme)} items={options([["system", "Use system setting"], ["light", "Light"], ["dark", "Dark"]])} />
        <Choice label="Table density" value={value.density} onChange={next => set("density", next as typeof value.density)} items={options([["comfortable", "Comfortable"], ["compact", "Compact"]])} />
      </div>
      <SettingToggle label="Reduce animations" description="Minimize transitions and loading animations." checked={value.reduce_motion} onChange={next => set("reduce_motion", next)} />
      <div className="space-y-5 border-t pt-5">
        <Choice label="Timezone" value={value.timezone} onChange={next => set("timezone", next)} items={zones} search />
        <div className="space-y-5">
          <Choice label="Date format" value={value.date_format} onChange={next => set("date_format", next as typeof value.date_format)} items={options([["locale", "Device format"], ["iso", "YYYY-MM-DD"], ["day-first", "DD/MM/YYYY"], ["month-first", "MM/DD/YYYY"]])} />
          <Choice label="Time format" value={value.time_format} onChange={next => set("time_format", next as typeof value.time_format)} items={options([["system", "Device format"], ["12", "12 hour"], ["24", "24 hour"]])} />
        </div>
        <p className="rounded-md bg-muted p-3 text-sm text-muted-foreground">Date preview: <span className="font-medium text-foreground" suppressHydrationWarning>{formatDate("2026-09-10T14:30:00Z", value)}</span></p>
      </div>
    </>;
  }}</SettingsForm>;
}
function DefaultsForm() {
  const { settings, save, resetTables } = useSettings(); const [resetting, setResetting] = useState(false);
  return <SettingsForm initial={settings.defaults} defaults={defaultSettings.defaults} onSave={value => save("defaults", value)}>{(value, change) => {
    const set = <K extends keyof Settings["defaults"]>(key: K, next: Settings["defaults"][K]) => change({ ...value, [key]: next });
    return <>
      <div className="space-y-5">
        <Choice label="Refresh interval" value={value.refresh_seconds} onChange={next => set("refresh_seconds", Number(next) as typeof value.refresh_seconds)} items={[0, 5, 10, 15, 30, 60].map(n => ({ value: String(n), label: n === 0 ? "Off" : n === 60 ? "1 minute" : `${n} seconds` }))} />
        <Choice label="Rows per page" value={value.page_size} onChange={next => set("page_size", Number(next) as typeof value.page_size)} items={[10, 25, 50, 100].map(n => ({ value: String(n), label: String(n) }))} />
        <Choice label="Page after sign-in" value={value.landing_page} onChange={next => set("landing_page", next as typeof value.landing_page)} items={options([["/", "Overview"], ["/content/articles", "Articles"], ["/taxonomy/topics", "Topics"], ["/jobs/analysis", "AI analysis"]])} />
        <Choice label="Overview date range" value={value.overview_days} onChange={next => set("overview_days", Number(next) as typeof value.overview_days)} items={options([["7", "7 days"], ["30", "30 days"]])} />
      </div>
      <div className="space-y-5 border-t pt-5"><h2 className="text-sm font-semibold">Remember my tables</h2>
        <SettingToggle label="Column visibility" checked={value.remember_columns} onChange={next => set("remember_columns", next)} />
        <SettingToggle label="Filters and searches" description="Restore your last view when opening a table. Links with explicit filters always take priority." checked={value.remember_filters} onChange={next => set("remember_filters", next)} />
        <SettingToggle label="Sort order" checked={value.remember_sort} onChange={next => set("remember_sort", next)} />
        <Button type="button" variant="outline" size="sm" disabled={!Object.keys(settings.tables).length} loading={resetting} onClick={async () => { setResetting(true); try { await resetTables(); notify.success("Saved table views cleared"); } catch (error) { notifyFailure(error, "Could not clear table views"); } finally { setResetting(false); } }}>Clear saved table views</Button>
      </div>
    </>;
  }}</SettingsForm>;
}
