"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";

export type CopyStatus = "idle" | "copying" | "copied" | "failed";

export function CopyButton({
  text,
  label,
  className,
  disabled,
  children,
  onStatusChange,
}: {
  text: string;
  label: string;
  className?: string;
  disabled?: boolean;
  children?: (status: CopyStatus) => ReactNode;
  onStatusChange?: (status: CopyStatus) => void;
}) {
  const [status, setStatus] = useState<CopyStatus>("idle");
  const operation = useRef(0);
  const button = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const token = operation;
    return () => {
      token.current++;
    };
  }, []);
  async function copy() {
    if (status === "copying") return;
    const token = ++operation.current;
    const update = (value: CopyStatus) => {
      if (operation.current !== token) return;
      setStatus(value);
      onStatusChange?.(value);
    };
    update("copying");
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(text);
      update("copied");
    } catch {
      if (operation.current !== token) return;
      const field = document.createElement("textarea");
      const focused = document.activeElement;
      field.value = text;
      field.readOnly = true;
      field.style.cssText = "position:fixed;opacity:0;pointer-events:none;width:1px;height:1px";
      // Stay inside the current dialog/popover so focus scopes permit selection.
      (button.current?.parentElement ?? document.body).appendChild(field);
      let copied = false;
      try {
        field.focus();
        field.select();
        copied = document.execCommand("copy");
      } catch {
        // Leave manual copy available when both clipboard methods are denied.
      } finally {
        field.remove();
        if (focused instanceof HTMLElement) focused.focus();
      }
      update(copied ? "copied" : "failed");
    }
  }
  return (
    <button
      ref={button}
      type="button"
      className={className}
      aria-label={label}
      disabled={disabled || status === "copying"}
      onClick={() => void copy()}
    >
      {children ? (
        children(status)
      ) : (
        <>
          {status === "copied" ? (
            <Check size={15} aria-hidden="true" />
          ) : (
            <Copy size={15} aria-hidden="true" />
          )}
          {label}
        </>
      )}
    </button>
  );
}
