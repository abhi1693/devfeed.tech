"use client";

import { Combobox, type ComboboxProps } from "./combobox";
import { languages, regionalLanguages } from "@/lib/languages";

type Props = Omit<ComboboxProps, "options" | "label"> & { label?: string };

export function LanguageSelect({ value, label = "Language", ...props }: Props) {
  const current = value.toLowerCase();
  const options = [
    ...languages.map((option) => ({ ...option, meta: option.value, group: "Languages" })),
    ...regionalLanguages(current).map((option) => ({
      ...option,
      meta: option.value,
      group: "Regional and other variants",
    })),
  ];
  return (
    <Combobox
      {...props}
      label={label}
      value={current}
      options={options}
      placeholder={props.required ? "Select a language…" : "Unknown / not specified"}
      clearLabel="Unknown / not specified"
      searchPlaceholder="Search languages or codes…"
      emptyMessage="No matching languages."
    />
  );
}
