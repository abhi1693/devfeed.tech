"use client";
import { Button } from "@/components/atoms/button";
import { FormField } from "@/components/molecules/form-field";
import type { EditableFact } from "@/lib/form-values";
export function FactsEditor({ facts, onChange, disabled = false, readOnly = false }: { facts: EditableFact[]; onChange: (facts: EditableFact[]) => void; disabled?: boolean; readOnly?: boolean }) {
  return <section className="space-y-4"><div><h2 className="font-semibold">Sourced facts</h2><p className="mt-1 text-xs text-muted-foreground">Release dates, founding dates, and other facts must include a source and retrieval date.</p></div>
    {facts.map((fact, index) => <fieldset key={index} disabled={disabled || readOnly} className="space-y-3 rounded-md border p-4"><legend className="px-1 text-sm">Fact {index + 1}</legend><div className="grid gap-4 sm:grid-cols-2">{(["name", "value", "source_url", "retrieved_at"] as const).map(key => <FormField key={key} field={{ key: `fact-${index}-${key}`, label: { name: "Fact name", value: "Value", source_url: "Source URL", retrieved_at: "Retrieved at" }[key], type: key === "source_url" ? "url" : key === "retrieved_at" ? "datetime" : "text", required: true, max: key === "name" ? 100 : key === "value" ? 500 : key === "source_url" ? 2048 : undefined }} value={fact[key]} disabled={disabled || readOnly} onChange={value => onChange(facts.map((item, i) => i === index ? { ...item, [key]: String(value) } : item))} />)}</div>{!readOnly && <Button type="button" variant="destructive-ghost" size="sm" onClick={() => onChange(facts.filter((_, i) => i !== index))}>Remove fact</Button>}</fieldset>)}
    {!readOnly && <Button type="button" variant="outline" disabled={disabled || facts.length >= 50} onClick={() => onChange([...facts, { name: "", value: "", source_url: "", retrieved_at: "" }])}>Add fact</Button>}
  </section>;
}
