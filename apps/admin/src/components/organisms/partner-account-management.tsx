"use client";
import { useEffect, useState } from "react";
import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import { Textarea } from "@/components/atoms/textarea";
import type {
  AccountOut as Account,
  AssetMetrics as Asset,
  AdminIdentity as Identity,
} from "@/lib/api/generated/models";

export function PartnerAccountManagement({
  identity,
  account,
  assets,
  onChange,
}: {
  identity: Identity;
  account: Account | null;
  assets: Asset[];
  onChange: () => void;
}) {
  const [members, setMembers] = useState<{ subject: string; issuer: string }[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!account) return;
    const controller = new AbortController();
    fetch(`/api/v1/admin/partner-accounts/${account.id}/members`, {
      signal: controller.signal,
      cache: "no-store",
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("Could not load memberships.");
        return response.json();
      })
      .then((value) => {
        if (!controller.signal.aborted) setMembers(value);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setMessage(error.message);
      });
    return () => controller.abort();
  }, [account, revision]);
  async function write(path: string, method: string, payload?: unknown) {
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch(`/api/v1/admin/partner-accounts${path}`, {
        method,
        headers: { "content-type": "application/json", "x-csrf-token": identity.csrf_token },
        body: payload === undefined ? undefined : JSON.stringify(payload),
      });
      if (!response.ok)
        throw new Error(
          response.status === 422
            ? "Check the form values and catalog product ID."
            : "Could not save changes. Please retry.",
        );
      setMessage("Changes saved.");
      setRevision((value) => value + 1);
      onChange();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not save changes.");
    } finally {
      setBusy(false);
    }
  }
  const values = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    return new FormData(event.currentTarget);
  };
  function accountValues(form: FormData) {
    return {
      name: form.get("name"),
      tier: form.get("tier"),
      status: form.get("status") ?? "active",
      benefits: String(form.get("benefits") ?? "")
        .split("\n")
        .map((value) => value.trim())
        .filter(Boolean),
    };
  }
  return (
    <section
      id="management"
      className="partner-account-management space-y-4 rounded-lg border bg-card p-5"
    >
      <div className="text-xs font-medium text-muted-foreground">Superuser tools</div>
      <h2 className="text-base font-semibold">Manage partnerships</h2>
      <p className="text-sm text-muted-foreground">
        Account memberships control access to data. Assign the partner role in Zitadel before adding
        a user here.
      </p>
      {message && (
        <p role="status" className="rounded-md border bg-muted p-3 text-sm">
          {message}
        </p>
      )}
      <details className="border-t py-4">
        <summary className="cursor-pointer text-sm font-medium">Create partner account</summary>
        <form
          className="mt-4 grid max-w-xl gap-4"
          onSubmit={(event) => {
            const form = values(event);
            void write("", "POST", accountValues(form));
          }}
        >
          <label className="flex flex-col gap-2 text-sm font-medium">
            Partner name
            <Input name="name" required maxLength={200} />
          </label>
          <label className="flex flex-col gap-2 text-sm font-medium">
            Partnership tier
            <Input name="tier" required maxLength={100} />
          </label>
          <label className="flex flex-col gap-2 text-sm font-medium">
            Benefits, one per line
            <Textarea name="benefits" />
          </label>
          <Button type="submit" loading={busy}>
            Create account
          </Button>
        </form>
      </details>
      {account && (
        <div key={account.id}>
          <details className="border-t py-4">
            <summary className="cursor-pointer text-sm font-medium">
              Edit {account.name} partnership
            </summary>
            <form
              className="mt-4 grid max-w-xl gap-4"
              onSubmit={(event) => {
                const form = values(event);
                void write(`/${account.id}`, "PUT", accountValues(form));
              }}
            >
              <label className="flex flex-col gap-2 text-sm font-medium">
                Partner name
                <Input name="name" required defaultValue={account.name} maxLength={200} />
              </label>
              <label className="flex flex-col gap-2 text-sm font-medium">
                Partnership tier
                <Input name="tier" required defaultValue={account.tier} maxLength={100} />
              </label>
              <label className="flex flex-col gap-2 text-sm font-medium">
                Status
                <select
                  className="h-9 rounded-md border border-input bg-background px-3 text-sm"
                  name="status"
                  defaultValue={account.status}
                >
                  <option value="active">Active</option>
                  <option value="paused">Paused</option>
                </select>
              </label>
              <label className="flex flex-col gap-2 text-sm font-medium">
                Benefits, one per line
                <Textarea name="benefits" defaultValue={(account.benefits ?? []).join("\n")} />
              </label>
              <Button type="submit" loading={busy}>
                Save partnership
              </Button>
            </form>
          </details>
          <details className="border-t py-4">
            <summary className="cursor-pointer text-sm font-medium">Account members</summary>
            <p>
              Use the user’s immutable Zitadel subject ID. An email address does not grant access.
            </p>
            <ul>
              {members.map((member) => (
                <li key={`${member.issuer}/${member.subject}`}>
                  {member.subject}{" "}
                  <Button
                    disabled={busy}
                    onClick={() =>
                      void write(
                        `/${account.id}/members/${encodeURIComponent(member.subject)}`,
                        "DELETE",
                      )
                    }
                  >
                    Remove member {member.subject}
                  </Button>
                </li>
              ))}
            </ul>
            {!members.length && <p>No members added yet.</p>}
            <form
              className="mt-4 grid max-w-xl gap-4"
              onSubmit={(event) => {
                const form = values(event);
                void write(`/${account.id}/members`, "PUT", {
                  subject: form.get("subject"),
                });
              }}
            >
              <label className="flex flex-col gap-2 text-sm font-medium">
                Zitadel subject ID
                <Input name="subject" required maxLength={200} />
              </label>
              <Button type="submit" loading={busy}>
                Add member
              </Button>
            </form>
          </details>
          <details className="border-t py-4">
            <summary className="cursor-pointer text-sm font-medium">
              Associate product or ad
            </summary>
            <form
              className="mt-4 grid max-w-xl gap-4"
              onSubmit={(event) => {
                const form = values(event);
                void write(`/${account.id}/assets`, "POST", {
                  name: form.get("name"),
                  kind: form.get("kind"),
                  product_id: form.get("product_id") || null,
                  status: form.get("status"),
                });
              }}
            >
              <label className="flex flex-col gap-2 text-sm font-medium">
                Asset name
                <Input name="name" required maxLength={200} />
              </label>
              <label className="flex flex-col gap-2 text-sm font-medium">
                Type
                <select
                  className="h-9 rounded-md border border-input bg-background px-3 text-sm"
                  name="kind"
                >
                  <option value="product">Product placement</option>
                  <option value="ad">Ad</option>
                </select>
              </label>
              <label className="flex flex-col gap-2 text-sm font-medium">
                Catalog product ID (required for product placements)
                <Input name="product_id" />
              </label>
              <label className="flex flex-col gap-2 text-sm font-medium">
                Status
                <select
                  className="h-9 rounded-md border border-input bg-background px-3 text-sm"
                  name="status"
                >
                  <option value="draft">Draft</option>
                  <option value="active">Active</option>
                  <option value="paused">Paused</option>
                  <option value="ended">Ended</option>
                </select>
              </label>
              <Button type="submit" loading={busy}>
                Associate asset
              </Button>
            </form>
          </details>
          {assets.map((asset) => (
            <details className="border-t py-4" key={asset.id}>
              <summary className="cursor-pointer text-sm font-medium">Edit {asset.name}</summary>
              <form
                className="mt-4 grid max-w-xl gap-4"
                onSubmit={(event) => {
                  const form = values(event);
                  void write(`/${account.id}/assets/${asset.id}`, "PUT", {
                    name: form.get("name"),
                    kind: asset.kind,
                    product_id: asset.product_id,
                    status: form.get("status"),
                  });
                }}
              >
                <label className="flex flex-col gap-2 text-sm font-medium">
                  Asset name
                  <Input name="name" required maxLength={200} defaultValue={asset.name} />
                </label>
                <label className="flex flex-col gap-2 text-sm font-medium">
                  Status
                  <select
                    className="h-9 rounded-md border border-input bg-background px-3 text-sm"
                    name="status"
                    defaultValue={asset.status}
                  >
                    <option value="draft">Draft</option>
                    <option value="active">Active</option>
                    <option value="paused">Paused</option>
                    <option value="ended">Ended</option>
                  </select>
                </label>
                <Button type="submit" loading={busy}>
                  Save asset
                </Button>
              </form>
            </details>
          ))}
        </div>
      )}
    </section>
  );
}
