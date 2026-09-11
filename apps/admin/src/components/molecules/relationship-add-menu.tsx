"use client";

import Link from "next/link";
import { ChevronDown, ClipboardCheck, Plus, Sparkles } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/atoms/dropdown-menu";

export function RelationshipAddMenu() {
  const base = "/taxonomy/relationships";
  return <DropdownMenu>
    <DropdownMenuTrigger asChild><Button size="sm"><Plus aria-hidden />Add relationship<ChevronDown aria-hidden /></Button></DropdownMenuTrigger>
    <DropdownMenuContent aria-label={"Add relationship options"}>
      <DropdownMenuItem asChild><Link href={`${base}/new`} prefetch={false}><Plus aria-hidden />Create relationship</Link></DropdownMenuItem>
      <DropdownMenuItem asChild><Link href={`${base}/discover`} prefetch={false}><Sparkles aria-hidden />Discover with AI</Link></DropdownMenuItem>
      <DropdownMenuItem asChild><Link href={`${base}/proposals`} prefetch={false}><ClipboardCheck aria-hidden />Review proposals</Link></DropdownMenuItem>
    </DropdownMenuContent>
  </DropdownMenu>;
}
