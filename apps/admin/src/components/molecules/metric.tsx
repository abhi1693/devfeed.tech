import { Card, CardContent } from "@/components/atoms/card";

export function Metric({ label, value }: { label: string; value: number }) {
  return <Card className="gap-0 py-4 shadow-none"><CardContent>
    <p className="text-sm text-muted-foreground">{label}</p>
    <p className="mt-1 text-2xl font-semibold tabular-nums">{value.toLocaleString("en")}</p>
  </CardContent></Card>;
}
