"use client";

import { useEffect, useRef, useState } from "react";
import { Download, LoaderCircle } from "lucide-react";
import { devCardData, devCardPng } from "@/lib/dev-card";
import type { UserIdentity, UserProfile } from "@/lib/user";
import { DevCardSharing } from "./dev-card-sharing";
import { trackEvent } from "@/lib/analytics";
import { DevCardArtwork } from "./dev-card-artwork";

function CardExport({
  current,
  user,
  unsaved,
}: {
  current: UserProfile;
  user: UserIdentity;
  unsaved: boolean;
}) {
  const svg = useRef<SVGSVGElement>(null);
  const [busy, setBusy] = useState(false);
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
    if (!svg.current || busy || unsaved) return;
    const token = ++operation.current;
    setBusy(true);
    setError(false);
    setMessage("");
    try {
      const { blob, avatarOmitted } = await devCardPng(svg.current);
      if (operation.current !== token) return;
      {
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = `devfeed-${current?.username || "dev-card"}.png`;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
      trackEvent("dev_card_share", { method: "download" });
      setMessage(
        `Your card is downloaded.${avatarOmitted ? " Used initials because your photo host doesn’t allow image export." : ""}`,
      );
    } catch {
      if (operation.current !== token) return;
      setError(true);
      setMessage("Couldn’t create your image. Please try again.");
    } finally {
      if (operation.current === token) setBusy(false);
    }
  }
  return (
    <>
      <DevCardArtwork data={devCardData(current, user)} svgRef={svg} />
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
          {busy ? "Creating image…" : "Download card"}
        </button>
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
}: {
  profile: UserProfile;
  user: UserIdentity;
  unsaved: boolean;
}) {
  return (
    <aside className="dev-card-preview" aria-label="Dev card preview">
      <div className="dev-card-preview-heading">
        <h3>Your Dev Card</h3>
      </div>
      <CardExport key={JSON.stringify(profile)} current={profile} user={user} unsaved={unsaved} />
      <DevCardSharing
        key={JSON.stringify([profile.username, profile.visibility?.public, unsaved])}
        profile={profile}
        unsaved={unsaved}
      />
    </aside>
  );
}
