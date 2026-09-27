"use client";

import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import { siX } from "simple-icons";
import brandMark from "@devfeed/theme/assets/devfeed-mark.png";
import { Code2, Download, LoaderCircle } from "lucide-react";
import { ChoiceGroup, ChoiceItem } from "@devfeed/ui/choice-controls";
import { devCardData, devCardPng } from "@/lib/dev-card";
import type { UserIdentity, UserProfile } from "@/lib/user";
import { DevCardSharing } from "./dev-card-sharing";
import { trackEvent } from "@/lib/analytics";
import { readerWebsiteLink } from "@/lib/reader-runtime";
import { DevCardArtwork } from "./dev-card-artwork";
import { DevCardXHeader } from "./dev-card-x-header";

export type DevCardPreviewFormat = "card" | "x-header" | "embed";

function CardExport({
  current,
  user,
  unsaved,
  format,
}: {
  format: "card" | "x-header";
  current: UserProfile;
  user: UserIdentity;
  unsaved: boolean;
}) {
  const svg = useRef<SVGSVGElement>(null);
  const [busy, setBusy] = useState<"card" | "x-header" | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState(false);
  const operation = useRef(0);
  useEffect(() => {
    const pending = operation;
    return () => {
      pending.current++;
    };
  }, []);

  async function exportCard() {
    const artwork = svg.current;
    if (!artwork || busy || unsaved) return;
    const token = ++operation.current;
    setBusy(format);
    setError(false);
    setMessage("");
    try {
      const { blob, avatarOmitted } = await devCardPng(artwork, format === "x-header" ? 1 : 2);
      if (operation.current !== token) return;
      {
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = `devfeed-${current?.username || "dev-card"}${format === "x-header" ? "-x-header" : ""}.png`;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
      trackEvent("dev_card_share", {
        method: format === "x-header" ? "download_x_header" : "download",
      });
      setMessage(
        `Your ${format === "x-header" ? "X header" : "card"} is downloaded.${avatarOmitted ? " Your photo couldn’t be included." : ""}`,
      );
    } catch {
      if (operation.current !== token) return;
      setError(true);
      setMessage("Couldn’t create your image. Please try again.");
    } finally {
      if (operation.current === token) setBusy(null);
    }
  }
  return (
    <>
      {format === "card" ? (
        <DevCardArtwork data={devCardData(current, user)} svgRef={svg} />
      ) : (
        <DevCardXHeader data={devCardData(current, user)} svgRef={svg} />
      )}
      {unsaved && (
        <p className="dev-card-share-note">Unsaved preview. Save your profile before sharing.</p>
      )}
      <div className="dev-card-actions">
        <button
          type="button"
          className="dev-card-download"
          disabled={Boolean(busy) || unsaved}
          onClick={() => void exportCard()}
        >
          {busy ? <LoaderCircle className="dev-card-spinner" size={17} /> : <Download size={17} />}
          {busy ? "Creating image…" : format === "card" ? "Download card" : "Download X header"}
        </button>
        {current.username && current.visibility?.public && !unsaved && (
          <a
            className="dev-card-profile-link"
            {...readerWebsiteLink(`/users/${encodeURIComponent(current.username)}`)}
          >
            View public profile
          </a>
        )}
      </div>
      <p
        className={`dev-card-status${error ? " dev-card-error" : ""}`}
        role={error ? "alert" : "status"}
      >
        {message}
      </p>
    </>
  );
}

export function DevCardPreview({
  profile,
  user,
  unsaved,
  format: selectedFormat,
  onFormatChange,
}: {
  profile: UserProfile;
  user: UserIdentity;
  unsaved: boolean;
  format?: DevCardPreviewFormat;
  onFormatChange?: (format: DevCardPreviewFormat) => void;
}) {
  const [localFormat, setLocalFormat] = useState<DevCardPreviewFormat>("card");
  const format = selectedFormat ?? localFormat;
  return (
    <aside data-preview-format={format} className="dev-card-preview" aria-label="Dev card preview">
      <ChoiceGroup
        className="dev-card-preview-options"
        aria-label="Preview format"
        orientation="horizontal"
        value={format}
        onValueChange={(value) => {
          const next = value === "x-header" || value === "embed" ? value : "card";
          setLocalFormat(next);
          onFormatChange?.(next);
        }}
      >
        <ChoiceItem value="card">
          <Image src={brandMark} alt="" width={16} height={16} />
          Dev Card
        </ChoiceItem>
        <ChoiceItem value="x-header">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true">
            <path d={siX.path} />
          </svg>
          Header
        </ChoiceItem>
        <ChoiceItem value="embed">
          <Code2 size={14} aria-hidden="true" />
          Embed
        </ChoiceItem>
      </ChoiceGroup>
      {format === "embed" ? (
        <DevCardSharing
          key={JSON.stringify([profile.username, profile.visibility?.public, unsaved])}
          profile={profile}
          unsaved={unsaved}
        />
      ) : (
        <CardExport
          key={`${format}:${JSON.stringify(profile)}`}
          format={format}
          current={profile}
          user={user}
          unsaved={unsaved}
        />
      )}
    </aside>
  );
}
