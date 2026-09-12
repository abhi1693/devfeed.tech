"use client";

import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { LoaderCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { Slot } from "radix-ui";

const buttonVariants = cva(
  "inline-flex shrink-0 cursor-pointer items-center justify-center gap-2 rounded-md text-sm font-medium whitespace-nowrap transition-[color,background-color,border-color,box-shadow] duration-150 motion-reduce:transition-none outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 aria-disabled:cursor-not-allowed aria-disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        default:
          "bg-primary text-primary-foreground not-disabled:not-aria-disabled:hover:bg-primary/90 not-disabled:not-aria-disabled:active:bg-primary/80",
        destructive:
          "bg-destructive text-white not-disabled:not-aria-disabled:hover:bg-destructive/90 not-disabled:not-aria-disabled:active:bg-destructive/80 focus-visible:ring-destructive/20 dark:bg-destructive/60 dark:focus-visible:ring-destructive/40",
        "destructive-ghost":
          "text-destructive not-disabled:not-aria-disabled:hover:bg-destructive/10 not-disabled:not-aria-disabled:active:bg-destructive/20 focus-visible:ring-destructive/20",
        outline:
          "border bg-background shadow-xs not-disabled:not-aria-disabled:hover:bg-accent not-disabled:not-aria-disabled:hover:text-accent-foreground not-disabled:not-aria-disabled:active:bg-accent/80 dark:border-input dark:bg-input/30 dark:not-disabled:not-aria-disabled:hover:bg-input/50",
        secondary:
          "bg-secondary text-secondary-foreground not-disabled:not-aria-disabled:hover:bg-secondary/80 not-disabled:not-aria-disabled:active:bg-secondary/60",
        ghost:
          "not-disabled:not-aria-disabled:hover:bg-accent not-disabled:not-aria-disabled:hover:text-accent-foreground not-disabled:not-aria-disabled:active:bg-accent/80 dark:not-disabled:not-aria-disabled:hover:bg-accent/50",
        link: "text-primary underline-offset-4 not-disabled:not-aria-disabled:hover:underline not-disabled:not-aria-disabled:active:text-primary/80",
      },
      size: {
        default: "h-9 px-4 py-2 has-[>svg]:px-3",
        xs: "h-6 gap-1 rounded-md px-2 text-xs has-[>svg]:px-1.5 [&_svg:not([class*='size-'])]:size-3",
        sm: "h-8 gap-1.5 rounded-md px-3 has-[>svg]:px-2.5",
        lg: "h-10 rounded-md px-6 has-[>svg]:px-4",
        icon: "size-9",
        "icon-xs": "size-6 rounded-md [&_svg:not([class*='size-'])]:size-3",
        "icon-sm": "size-8",
        "icon-lg": "size-10",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export type ButtonProps = React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean;
    loading?: boolean;
    /** Native button text while loading; slotted links retain their own contents. */
    loadingText?: string;
  };

function Button({
  className,
  variant = "default",
  size = "default",
  asChild = false,
  loading = false,
  loadingText,
  disabled = false,
  type,
  children,
  onClickCapture,
  onAuxClickCapture,
  onKeyDownCapture,
  ...props
}: ButtonProps) {
  const Comp = asChild ? Slot.Root : "button";
  const blocked =
    disabled || loading || props["aria-disabled"] === true || props["aria-disabled"] === "true";
  function preventActivation(event: React.SyntheticEvent<HTMLElement>) {
    if (!blocked && !event.currentTarget.matches(":disabled")) return false;
    event.preventDefault();
    event.stopPropagation();
    return true;
  }

  return (
    <Comp
      {...props}
      data-slot="button"
      data-variant={variant}
      data-size={size}
      data-loading={loading || undefined}
      type={asChild ? type : (type ?? "button")}
      disabled={asChild ? undefined : blocked}
      aria-disabled={blocked || props["aria-disabled"]}
      aria-busy={loading || props["aria-busy"]}
      tabIndex={asChild && blocked ? -1 : props.tabIndex}
      className={cn(buttonVariants({ variant, size }), className)}
      onClickCapture={(event) => {
        if (!preventActivation(event)) onClickCapture?.(event);
      }}
      onAuxClickCapture={(event) => {
        if (!preventActivation(event)) onAuxClickCapture?.(event);
      }}
      onKeyDownCapture={(event) => {
        if (["Enter", " "].includes(event.key) && preventActivation(event)) return;
        onKeyDownCapture?.(event);
      }}
    >
      {loading && (
        <LoaderCircle
          data-slot="button-spinner"
          aria-hidden
          className="size-4 animate-spin motion-reduce:animate-none"
        />
      )}
      {asChild ? (
        <Slot.Slottable>{children}</Slot.Slottable>
      ) : loading && size?.startsWith("icon") ? null : loading && loadingText ? (
        loadingText
      ) : (
        children
      )}
    </Comp>
  );
}

export { Button, buttonVariants };
