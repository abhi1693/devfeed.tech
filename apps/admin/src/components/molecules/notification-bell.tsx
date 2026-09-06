import { Bell } from "lucide-react";
import { Button } from "@/components/atoms/button";
import type { ComponentProps } from "react";

export function NotificationBell({ count = 0, ...props }: ComponentProps<typeof Button> & { count?: number }) {
  return <Button variant="outline" size="icon" className="relative shrink-0"
    aria-label={count ? `Notifications (${count} new)` : "Notifications"} {...props}>
    <Bell aria-hidden />
    {count > 0 && <span aria-hidden className="absolute -right-1.5 -top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-medium text-primary-foreground">{count > 99 ? "99+" : count}</span>}
  </Button>;
}
