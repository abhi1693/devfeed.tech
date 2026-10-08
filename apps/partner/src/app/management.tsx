"use client";
import { useEffect, useState } from "react";
import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import { Textarea } from "@/components/atoms/textarea";
import type { Account, Asset, Identity } from "@/lib/types";

export function Management({
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
    fetch(`/api/v1/partner/accounts/${account.id}/members`, {
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
      const response = await fetch(`/api/v1/partner/${path}`, {
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
    <section id="management" className="card management scroll-mt-6">
      <div className="eyebrow">Superuser tools</div>
      <h2>Manage partnerships</h2>
      <p className="muted">
        Account memberships control access to data. Assign the partner role in Zitadel before adding
        a user here.
      </p>
      {message && (
        <p role="status" className="notice">
          {message}
        </p>
      )}
      <details>
        <summary>Create partner account</summary>
        <form
          onSubmit={(event) => {
            const form = values(event);
            void write("accounts", "POST", accountValues(form));
          }}
        >
          <label>
            Partner name
            <Input name="name" required maxLength={200} />
          </label>
          <label>
            Partnership tier
            <Input name="tier" required maxLength={100} />
          </label>
          <label>
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
          <details>
            <summary>Edit {account.name} partnership</summary>
            <form
              onSubmit={(event) => {
                const form = values(event);
                void write(`accounts/${account.id}`, "PUT", accountValues(form));
              }}
            >
              <label>
                Partner name
                <Input name="name" required defaultValue={account.name} maxLength={200} />
              </label>
              <label>
                Partnership tier
                <Input name="tier" required defaultValue={account.tier} maxLength={100} />
              </label>
              <label>
                Status
                <select name="status" defaultValue={account.status}>
                  <option value="active">Active</option>
                  <option value="paused">Paused</option>
                </select>
              </label>
              <label>
                Benefits, one per line
                <Textarea name="benefits" defaultValue={account.benefits.join("\n")} />
              </label>
              <Button type="submit" loading={busy}>
                Save partnership
              </Button>
            </form>
          </details>
          <details>
            <summary>Account members</summary>
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
                        `accounts/${account.id}/members/${encodeURIComponent(member.subject)}`,
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
              onSubmit={(event) => {
                const form = values(event);
                void write(`accounts/${account.id}/members`, "PUT", {
                  subject: form.get("subject"),
                });
              }}
            >
              <label>
                Zitadel subject ID
                <Input name="subject" required maxLength={200} />
              </label>
              <Button type="submit" loading={busy}>
                Add member
              </Button>
            </form>
          </details>
          <details>
            <summary>Associate product or ad</summary>
            <form
              onSubmit={(event) => {
                const form = values(event);
                void write(`accounts/${account.id}/assets`, "POST", {
                  name: form.get("name"),
                  kind: form.get("kind"),
                  product_id: form.get("product_id") || null,
                  status: form.get("status"),
                });
              }}
            >
              <label>
                Asset name
                <Input name="name" required maxLength={200} />
              </label>
              <label>
                Type
                <select name="kind">
                  <option value="product">Product placement</option>
                  <option value="ad">Ad</option>
                </select>
              </label>
              <label>
                Catalog product ID (required for product placements)
                <Input name="product_id" />
              </label>
              <label>
                Status
                <select name="status">
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
            <details key={asset.id}>
              <summary>Edit {asset.name}</summary>
              <form
                onSubmit={(event) => {
                  const form = values(event);
                  void write(`accounts/${account.id}/assets/${asset.id}`, "PUT", {
                    name: form.get("name"),
                    kind: asset.kind,
                    product_id: asset.product_id,
                    status: form.get("status"),
                  });
                }}
              >
                <label>
                  Asset name
                  <Input name="name" required maxLength={200} defaultValue={asset.name} />
                </label>
                <label>
                  Status
                  <select name="status" defaultValue={asset.status}>
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
