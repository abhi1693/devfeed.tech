"use client";

import { useCallback, useState } from "react";
import { CircleAlert, CircleCheck, ExternalLink, LoaderCircle, PlugZap, Unplug } from "lucide-react";
import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/atoms/popover";
import { adminAiConnection, adminAiLogin, adminAiLoginCancel } from "@/lib/api/generated/admin";
import { ApiError } from "@/lib/api/client";
import { notifyFailure } from "@/lib/notifications";
import { useRequest } from "@/lib/use-request";
import { cn } from "@/lib/utils";

const labels = {
  disabled: "AI off", checking: "Checking AI", unavailable: "AI unavailable",
  signed_out: "Connect AI", connected: "AI connected", limited: "AI usage limit", error: "AI needs attention",
};

/** One connection control across the admin app; credentials stay with Codex. */
export function AiConnection({ csrfToken }: { csrfToken: string }) {
  const [open, setOpen] = useState(false);
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string>();
  const load = useCallback((signal: AbortSignal) => adminAiConnection({ signal }), []);
  const request = useRequest(`ai-connection:${revision}`, load, 10_000);
  // A failed status request must never leave a stale green connection indicator.
  const state = request.error ? "error" : request.data?.state ?? "checking";
  const login = request.error ? undefined : request.data?.login;
  const pending = login?.status === "pending";
  const connected = state === "connected" || state === "limited";
  const Icon = state === "checking" ? LoaderCircle : state === "connected" ? CircleCheck
    : state === "disabled" ? Unplug : state === "signed_out" ? PlugZap : CircleAlert;
  const message = request.error ? "Could not check the AI connection. The admin service may be unavailable."
    : request.data?.message ?? "Checking Codex…";

  async function act(action: "login" | "cancel") {
    if (busy) return;
    setBusy(true); setFailure(undefined);
    try {
      const options = { headers: { "X-CSRF-Token": csrfToken } };
      if (action === "cancel" && login) await adminAiLoginCancel({ login_id: login.login_id }, options);
      else await adminAiLogin(options);
      setRevision(value => value + 1);
    } catch (error) {
      setFailure(error instanceof ApiError ? error.message : "Could not update the connection. Please try again.");
      notifyFailure(error, action === "cancel" ? "Could not cancel sign-in" : "Could not connect ChatGPT");
    } finally { setBusy(false); }
  }

  return <Popover open={open} onOpenChange={value => { setOpen(value); if (value) setRevision(value => value + 1); }}>
    <PopoverTrigger asChild>
      <Button variant="outline" size="sm" aria-label={`AI connection: ${labels[state]}`}>
        <Icon aria-hidden className={cn("size-4", state === "connected" && "text-emerald-600", state === "checking" && "animate-spin motion-reduce:animate-none", ["unavailable", "error", "limited", "signed_out"].includes(state) && "text-amber-600")} />
        <span>{labels[state]}</span>
      </Button>
    </PopoverTrigger>
    <PopoverContent align="end" className="w-96 max-w-[calc(100vw-24px)] space-y-4 p-4" aria-label="AI connection">
      <h2 className="font-semibold">AI connection</h2>
      <p role="status" className="text-sm text-muted-foreground">{message}</p>
      {request.data && state !== "disabled" && <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
        <dt className="text-muted-foreground">Server</dt>
        <dd>{state === "unavailable" ? "Unreachable" : request.error ? "Unknown" : state === "checking" ? "Checking…" : "Online"}</dd>
        <dt className="text-muted-foreground">Account</dt>
        <dd className="break-words">{connected ? request.data.email || "Connected" : state === "signed_out" ? "Sign-in required" : "Not verified"}</dd>
        {request.data.model && <><dt className="text-muted-foreground">Model</dt><dd className="break-all">{request.data.model}</dd></>}
      </dl>}
      {failure && <p role="alert" className="text-sm text-destructive">{failure}</p>}
      {pending && login.user_code && login.verification_url ? <div className="space-y-3 border-t pt-4">
        <label htmlFor="ai-device-code" className="block text-sm">Enter this code in ChatGPT</label>
        <Input id="ai-device-code" value={login.user_code} readOnly onFocus={event => event.target.select()}
          className="h-12 text-center font-mono text-xl tracking-widest" />
        <div className="flex flex-wrap gap-2">
          <Button asChild><a href={login.verification_url} target="_blank" rel="noopener noreferrer"><ExternalLink aria-hidden />Open ChatGPT</a></Button>
          <Button variant="outline" loading={busy} onClick={() => void act("cancel")}>Cancel</Button>
        </div>
        <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><LoaderCircle aria-hidden className="size-4 animate-spin motion-reduce:animate-none" />Waiting for approval in ChatGPT…</p>
      </div> : <>
        {login?.message && <p role="alert" className="text-sm text-destructive">{login.message}</p>}
        {login?.status === "completed" && connected && <p role="status" className="text-sm text-emerald-700">ChatGPT connected.</p>}
        {!request.error && ["signed_out", "error", "connected", "limited"].includes(state) && <Button loading={busy} loadingText="Starting sign-in…" onClick={() => void act("login")}>
          <PlugZap aria-hidden />{connected ? "Reconnect ChatGPT" : "Connect ChatGPT"}
        </Button>}
      </>}
      {["disabled", "unavailable"].includes(state) && <div className="space-y-2 border-t pt-3 text-sm">
        <p>{state === "disabled" ? "Enable the AI services, then connect your account here." : "On the Docker host, start the AI services:"}</p>
        <code className="block break-all rounded bg-muted p-2 text-xs">{state === "disabled" ? "python3 scripts/compose_dev.py --ai" : "docker compose --profile ai up -d codex-server codex-client"}</code>
      </div>}
    </PopoverContent>
  </Popover>;
}
