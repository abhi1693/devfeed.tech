"use client";
import { Plus, Trash2 } from "lucide-react";
import type { ProfileLink } from "@/lib/user";
import { ProfileLinkIcon } from "./profile-link-icon";
export function ProfileLinksEditor({
  links,
  onChange,
}: {
  links: ProfileLink[];
  onChange: (links: ProfileLink[]) => void;
}) {
  const rows = links.length ? links : [{ url: "", label: null }];
  return (
    <section className="profile-direct-section" aria-label="Links">
      <div className="profile-direct-section-heading">
        <h3>Links</h3>
        <button
          type="button"
          className="settings-button settings-button-ghost"
          disabled={links.length >= 20 || !rows.at(-1)?.url.trim()}
          onClick={() => onChange([...links, { url: "", label: null }])}
        >
          <Plus size={14} aria-hidden="true" />
          Add link
        </button>
      </div>
      <div className="profile-direct-links">
        {rows.map((link, index) => (
          <div className="profile-direct-link" key={index}>
            <div className="profile-direct-link-url">
              <input
                aria-label={`Link ${index + 1} URL`}
                type="url"
                maxLength={2048}
                placeholder="https://github.com/you"
                value={link.url}
                onChange={(event) =>
                  onChange(
                    rows.map((item, i) =>
                      i === index ? { url: event.target.value, label: null } : item,
                    ),
                  )
                }
              />
              <ProfileLinkIcon url={link.url} />
            </div>
            <button
              type="button"
              className="settings-icon-button"
              aria-label={`Remove link ${index + 1}`}
              disabled={!links.length}
              onClick={() => onChange(links.filter((_, i) => i !== index))}
            >
              <Trash2 size={15} aria-hidden="true" />
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}
