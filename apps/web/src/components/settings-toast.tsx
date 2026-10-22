"use client";

import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { CircleAlert, CircleCheck, X } from "lucide-react";
import { Toast } from "radix-ui";
import "./settings-toast.css";

type Variant = "success" | "error";
type Notify = (message: string, variant?: Variant) => void;
type Notification = { id: number; message: string; variant: Variant };

const Context = createContext<Notify>(() => {});
export const useSettingsToast = () => useContext(Context);

export function SettingsToastProvider({ children }: { children: ReactNode }) {
  const [notification, setNotification] = useState<Notification | null>(null);
  const notify = useCallback<Notify>((message, variant = "success") => {
    setNotification((previous) => ({ id: (previous?.id ?? 0) + 1, message, variant }));
  }, []);
  const Icon = notification?.variant === "error" ? CircleAlert : CircleCheck;

  return (
    <Context.Provider value={notify}>
      <Toast.Provider duration={5000}>
        {children}
        {notification && (
          <Toast.Root
            key={notification.id}
            className="settings-toast"
            data-variant={notification.variant}
            type={notification.variant === "error" ? "foreground" : "background"}
            open
            onOpenChange={(open) => {
              if (!open) setNotification(null);
            }}
          >
            <Icon className="settings-toast-icon" size={18} aria-hidden="true" />
            <Toast.Title className="settings-toast-message">{notification.message}</Toast.Title>
            <Toast.Close className="settings-toast-dismiss" aria-label="Dismiss notification">
              <X size={16} aria-hidden="true" />
            </Toast.Close>
          </Toast.Root>
        )}
        <Toast.Viewport className="settings-toast-viewport" />
      </Toast.Provider>
    </Context.Provider>
  );
}
