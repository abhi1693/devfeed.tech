"use client";
import { Input } from "@/components/atoms/input";
import { UrlInput } from "@/components/atoms/url-input";
import { Textarea } from "@/components/atoms/textarea";
import { Select } from "./select";
import { EntityPicker } from "./entity-picker";
import { LanguageSelect } from "./language-select";
import { LogoUrlField } from "./logo-url-field";
import { ImageUrlField } from "./image-url-field";
import { Field } from "./field";
import { type FieldSpec, humanize } from "@/lib/resources";
export function FormField({ field, value, onChange, error, disabled, exclude, loading, loadingText }: { field: FieldSpec; value: unknown; onChange: (value: unknown) => void; error?: string; disabled?: boolean; exclude?: string; loading?: boolean; loadingText?: string }) {
  const text = typeof value === "string" || typeof value === "number" ? String(value) : "";
  return <Field id={field.key} name={field.key} label={field.label} required={field.required} disabled={disabled} subtext={field.help} tooltip={field.tooltip} error={error}>
    {control => {
      if (field.type === "language") return <LanguageSelect {...control} label={field.label} value={text} onChange={onChange} />;
      if (field.type === "reference") return <EntityPicker {...control} label={field.label} resource={field.resource!} value={text} exclude={exclude} onChange={onChange} />;
      if (field.type === "select") return <Select {...control} label={field.label} value={text} onChange={onChange} options={(field.choices ?? []).map(choice => ({ value: choice, label: humanize(choice) }))} />;
      if (field.type === "boolean") return <Input {...control} type="checkbox" checked={!!value} onChange={event => onChange(event.target.checked)} />;
      if (field.type === "textarea" || field.type === "lines") return <Textarea {...control} value={text} onChange={event => onChange(event.target.value)} maxLength={field.max} rows={field.type === "lines" ? 3 : 6} />;
      if (field.type === "logo-url") return <LogoUrlField {...control} value={text} onChange={event => onChange(event.target.value)} maxLength={field.max} />;
      if (field.type === "image-url") return <ImageUrlField {...control} value={text} onChange={event => onChange(event.target.value)} maxLength={field.max} />;
      if (field.type === "url") return <UrlInput {...control} value={text} onChange={event => onChange(event.target.value)} maxLength={field.max} loading={loading} loadingText={loadingText} />;
      return <Input {...control} value={text} onChange={event => onChange(event.target.value)} type={field.type === "datetime" ? "datetime-local" : field.type ?? "text"} maxLength={field.type === "number" ? undefined : field.max} min={field.min} max={field.type === "number" ? field.max : undefined} step={field.step} />;
    }}
  </Field>;
}
