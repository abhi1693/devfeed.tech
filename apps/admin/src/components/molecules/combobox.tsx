"use client";

import { Select, type SelectOption, type SelectProps } from "./select";

export type ComboboxOption = SelectOption;
export type ComboboxProps = Omit<SelectProps, "search"> & {
  search?: string;
  onSearchChange?: (value: string) => void;
  searchPlaceholder?: string;
};

/** Searchable Select. Entity and language pickers add their own data sources. */
export function Combobox({ search, onSearchChange, searchPlaceholder, ...props }: ComboboxProps) {
  return <Select {...props} search={{ value: search, onChange: onSearchChange, placeholder: searchPlaceholder }} />;
}
