"use client";

import Link from "next/link";
import { ChevronDown, Plus, Sparkles } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/atoms/dropdown-menu";

export function TopicAddMenu({ relationships = false }: { relationships?: boolean }) {
  const label = relationships ? "relationship" : "topic";
  const base = relationships ? "/taxonomy/relationships" : "/taxonomy/topics";
  return <DropdownMenu>
    <DropdownMenuTrigger asChild><Button size="sm"><Plus aria-hidden />Add {label}<ChevronDown aria-hidden /></Button></DropdownMenuTrigger>
    <DropdownMenuContent aria-label={`Add ${label} options`}>
      <DropdownMenuItem asChild><Link href={`${base}/new`} prefetch={false}><Plus aria-hidden />Create {label}</Link></DropdownMenuItem>
      <DropdownMenuItem asChild><Link href={`${base}/${relationships ? "discover" : "proposals"}`} prefetch={false}><Sparkles aria-hidden />{relationships ? "Discover with AI" : "Discover and review"}</Link></DropdownMenuItem>
      {relationships && <DropdownMenuItem asChild><Link href={`${base}/proposals`} prefetch={false}>Review proposals</Link></DropdownMenuItem>}
    </DropdownMenuContent>
  </DropdownMenu>;
}
