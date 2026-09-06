import Link from "next/link";
import { ClipboardCheck, Pencil, RefreshCw, Tags, Trash2 } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/atoms/tooltip";
import { cn } from "@/lib/utils";
import { type Resource, resources } from "@/lib/resources";
export function RecordActions({ resource, id, detail = false }: { resource: Resource; id: string; detail?: boolean }) {
  const href = `/${resource}/${encodeURIComponent(id)}`;
  if (resources[resource].readonly) return null;
  return <div role="group" aria-label="Record actions" className={cn("flex items-center gap-1", detail && "flex-wrap gap-2")}>
    {detail && resource === "articles" && <>
      <Button variant="outline" size="sm" asChild><Link href={`${href}/classify`} prefetch={false}><Tags aria-hidden />Classify</Link></Button>
      <Button size="sm" asChild><Link href={`${href}/review`} prefetch={false}><ClipboardCheck aria-hidden />Review / publish</Link></Button>
    </>}
    {detail && resource === "sources" && <>
      <Button variant="outline" size="sm" asChild><Link href={`${href}/fetch`} prefetch={false}><RefreshCw aria-hidden />Fetch feed</Link></Button>
      <Button size="sm" asChild><Link href={`${href}/review`} prefetch={false}><ClipboardCheck aria-hidden />Review source</Link></Button>
    </>}
    <span className={cn("inline-flex items-center gap-1", detail && "gap-2")}>
      {([{ action: "edit", label: "Edit", Icon: Pencil, variant: "outline" }, { action: "delete", label: "Delete", Icon: Trash2, variant: "destructive-ghost" }] as const).map(({ action, label, Icon, variant }) => {
        const button = <Button key={action} variant={variant} size={detail ? "sm" : "icon-sm"} asChild><Link prefetch={false} href={`${href}/${action}`} aria-label={detail ? undefined : label}><Icon aria-hidden />{detail && label}</Link></Button>;
        return detail ? button : <Tooltip key={action}><TooltipTrigger asChild>{button}</TooltipTrigger><TooltipContent>{label}</TooltipContent></Tooltip>;
      })}
    </span>
  </div>;
}
