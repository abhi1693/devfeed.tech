"use client";

import { Check, Columns3, LayoutGrid, List, Rows3 } from "lucide-react";
import { DropdownMenu } from "radix-ui";
import { availableReaderLayouts, type FeedViewPreference } from "@/lib/reader-layouts";
import { useFeedPreferences } from "./feed-preferences";
import { useFeedViewScope } from "./scoped-feed-preferences";
import { useUser } from "./user-account";
import { useSettingsToast } from "./settings-toast";
import styles from "./feed-layout-menu.module.css";

const icons = { cards: LayoutGrid, compact: List, stacked: Rows3, columns: Columns3 };

/** This control changes the current feed without changing any neighboring feed. */
export function FeedLayoutMenu({
  compact = false,
  allowColumns = false,
}: {
  compact?: boolean;
  allowColumns?: boolean;
}) {
  const scope = useFeedViewScope();
  const { view, loading, busy, unavailable, setView } = useFeedPreferences();
  const { user, loading: checking } = useUser();
  const notify = useSettingsToast();
  if (!scope) return null;
  const layouts = availableReaderLayouts.filter(
    (layout) => layout.value !== "columns" || (scope === "my_feed" && !!user && allowColumns),
  );
  const selected = layouts.find((layout) => layout.value === view) ?? layouts[0];
  const Icon = icons[selected.value];
  return (
    <DropdownMenu.Root modal={false}>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          className={styles.trigger}
          aria-label={`Change feed layout: ${selected.label}`}
          title={`Layout: ${selected.label}`}
          disabled={loading || busy || unavailable || checking}
        >
          <Icon size={17} aria-hidden="true" />
          {!compact && <span className={styles.label}>{selected.label}</span>}
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          className={styles.menu}
          align="end"
          sideOffset={6}
          collisionPadding={12}
        >
          <DropdownMenu.RadioGroup
            value={selected.value}
            onValueChange={(value) => {
              if (value === selected.value) return;
              void setView(value as FeedViewPreference).then((saved) => {
                if (saved) notify("Layout saved for this feed.");
                else notify("Couldn’t save this feed’s layout. Please try again.", "error");
              });
            }}
          >
            {layouts.map(({ value, label }) => {
              const ItemIcon = icons[value];
              return (
                <DropdownMenu.RadioItem
                  key={value}
                  value={value}
                  disabled={loading || busy || unavailable || checking}
                >
                  <ItemIcon size={16} aria-hidden="true" />
                  <span>{label}</span>
                  <DropdownMenu.ItemIndicator className={styles.selected}>
                    <Check size={15} aria-hidden="true" />
                  </DropdownMenu.ItemIndicator>
                </DropdownMenu.RadioItem>
              );
            })}
          </DropdownMenu.RadioGroup>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
