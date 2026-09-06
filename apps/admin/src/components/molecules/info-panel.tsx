import { Card, CardContent, CardHeader, CardTitle } from "@/components/atoms/card";
import { humanize } from "@/lib/resources";
export function DataValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "") return <span className="text-muted-foreground">—</span>;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.length ? <ul className="space-y-2">{value.map((item, index) => <li key={index}><DataValue value={item} /></li>)}</ul> : <span className="text-muted-foreground">None</span>;
  if (typeof value === "object") return <dl className="space-y-2">{Object.entries(value).map(([key, item]) => <div key={key}><dt className="text-xs text-muted-foreground">{humanize(key)}</dt><dd><DataValue value={item} /></dd></div>)}</dl>;
  const text = String(value);
  if (/^https?:\/\//.test(text)) return <a href={text} target="_blank" rel="noopener noreferrer" className="break-all text-blue-700 hover:underline">{text}</a>;
  return <span className="whitespace-pre-wrap break-words">{text}</span>;
}
export function InfoPanel({ title, fields, children }: { title: string; fields?: { label: string; value: React.ReactNode }[]; children?: React.ReactNode }) {
  return <Card className="min-w-0 shadow-none"><CardHeader className="border-b"><CardTitle className="text-sm font-semibold">{title}</CardTitle></CardHeader><CardContent>{fields && <dl className="divide-y">{fields.map(field => <div key={field.label} className="grid gap-1 py-3 text-sm sm:grid-cols-[160px_minmax(0,1fr)] sm:gap-4"><dt className="text-muted-foreground">{field.label}</dt><dd className="min-w-0">{field.value}</dd></div>)}</dl>}{children}</CardContent></Card>;
}
