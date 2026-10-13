"use client";

import { useEffect, useState } from "react";
import { Combobox } from "@devfeed/ui/combobox";
import { Button } from "@devfeed/ui/button";
import { PartnerAccountsListResponse } from "@/lib/api/generated/validators";
import { usePartnerRequest, isPartnerRequestCancelled } from "@/lib/partner-request";
import type { Account, AccountPage } from "@/lib/types";

export function PartnerAccountPicker({
  account,
  onChange,
  onUnavailable,
  required = false,
  disabled = false,
  label = "Partner account",
  size = "default",
  className,
  searchPlaceholder,
}: {
  account?: Account;
  onChange: (account: Account | undefined) => void;
  onUnavailable: () => void;
  required?: boolean;
  disabled?: boolean;
  label?: string;
  size?: "default" | "sm";
  className?: string;
  searchPlaceholder?: string;
}) {
  const request = usePartnerRequest({ onUnavailable });
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<AccountPage>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const limit = 25;
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    async function load() {
      setLoading(true);
      setError("");
      try {
        const result = await request(
          `accounts?limit=${limit}&offset=${offset}&q=${encodeURIComponent(query)}`,
          {
            signal: controller.signal,
            errorMessage: "Could not load partner accounts. Please retry.",
            parse: (value) => PartnerAccountsListResponse.parse(value),
          },
        );
        if (!controller.signal.aborted) setPage(result);
      } catch (reason) {
        if (!controller.signal.aborted && !isPartnerRequestCancelled(reason))
          setError(reason instanceof Error ? reason.message : "Could not load partner accounts.");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    const timer = setTimeout(() => void load(), query ? 200 : 0);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [open, offset, query, refresh, request]);
  return (
    <Combobox
      name={required ? "account_id" : undefined}
      label={label}
      size={size}
      className={className}
      searchPlaceholder={searchPlaceholder}
      required={required}
      disabled={disabled}
      value={account?.id ?? ""}
      selectedOption={account ? { value: account.id, label: account.name } : undefined}
      placeholder={required ? "Choose a partner account" : "All accounts"}
      clearLabel="All accounts"
      options={(page?.items ?? []).map((item) => ({
        value: item.id,
        label: item.name,
        description: item.status === "paused" ? "Paused" : undefined,
      }))}
      onChange={(id) =>
        onChange(id ? (page?.items.find((item) => item.id === id) ?? account) : undefined)
      }
      onOpenChange={(next) => {
        setOpen(next);
        setQuery("");
        setOffset(0);
      }}
      search={query}
      onSearchChange={(next) => {
        setQuery(next);
        setOffset(0);
      }}
      loading={loading}
      error={error}
      onRetry={() => setRefresh((value) => value + 1)}
      footer={
        page && page.total > limit ? (
          <div className="flex items-center justify-between gap-2 border-t p-2 text-xs">
            <Button
              variant="ghost"
              size="sm"
              disabled={loading || offset === 0}
              onClick={() => setOffset(Math.max(0, offset - limit))}
            >
              Previous accounts
            </Button>
            <span>
              {offset + 1}–{Math.min(offset + limit, page.total)} of {page.total}
            </span>
            <Button
              variant="ghost"
              size="sm"
              disabled={loading || offset + limit >= page.total}
              onClick={() => setOffset(offset + limit)}
            >
              Next accounts
            </Button>
          </div>
        ) : undefined
      }
    />
  );
}
