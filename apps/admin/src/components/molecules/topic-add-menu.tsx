"use client";

import Link from "next/link";
import { ChevronDown, Plus, Sparkles } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/atoms/dropdown-menu";

export function TopicAddMenu() {
  return <DropdownMenu>
    <DropdownMenuTrigger asChild><Button size="sm"><Plus aria-hidden />Add topic<ChevronDown aria-hidden /></Button></DropdownMenuTrigger>
    <DropdownMenuContent aria-label="Add topic options">
      <DropdownMenuItem asChild><Link href="/taxonomy/topics/new" prefetch={false}><Plus aria-hidden />Create topic</Link></DropdownMenuItem>
      <DropdownMenuItem asChild><Link href="/taxonomy/topics/proposals" prefetch={false}><Sparkles aria-hidden />Discover and review</Link></DropdownMenuItem>
    </DropdownMenuContent>
  </DropdownMenu>;
}
