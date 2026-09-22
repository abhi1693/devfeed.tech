"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Copy, Download, LoaderCircle } from "lucide-react";
import { devCardData, devCardPng } from "@/lib/dev-card";
import type { UserIdentity, UserProfile } from "@/lib/user";
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
  const [busy, setBusy] = useState<"download" | "copy" | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState(false);
  const [copyAvailable] = useState(
    () =>
      typeof navigator !== "undefined" &&
      typeof navigator.clipboard?.write === "function" &&
      typeof ClipboardItem !== "undefined",
  );
  const operation = useRef(0);
  useEffect(() => {
    const pending = operation;
    return () => {
      pending.current++;
    };
  }, []);

  async function exportCard(action: "download" | "copy") {
    if (!svg.current || busy || unsaved) return;
    const token = ++operation.current;
    setBusy(action);
    setError(false);
    setMessage("");
    try {
      const image = devCardPng(svg.current);
      void image.catch(() => {});
      // Start clipboard.write during the click so browser activation is retained.
      if (action === "copy") {
        const png = image.then(({ blob }) => blob);
        void png.catch(() => {});
        await navigator.clipboard.write([new ClipboardItem({ "image/png": png })]);
      }
      const { blob, avatarOmitted } = await image;
      if (operation.current !== token) return;
      if (action === "download") {
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = `devfeed-${current?.username || "dev-card"}.png`;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
      setMessage(
        `${action === "copy" ? "Image copied. Ready to paste." : "Your card is downloaded."}${avatarOmitted ? " Used initials because your photo host doesn’t allow image export." : ""}`,
      );
    } catch {
      if (operation.current !== token) return;
      setError(true);
      setMessage(
        action === "copy"
          ? "Couldn’t copy the image. Try downloading your card instead."
          : "Couldn’t create your image. Please try again.",
      );
    } finally {
      if (operation.current === token) setBusy(null);
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
          onClick={() => void exportCard("download")}
        >
          {busy === "download" ? (
            <LoaderCircle className="dev-card-spinner" size={17} />
          ) : (
            <Download size={17} />
          )}
          {busy === "download" ? "Creating image…" : "Download card"}
        </button>
        {copyAvailable && (
          <button
            type="button"
            disabled={Boolean(busy) || unsaved}
            onClick={() => void exportCard("copy")}
          >
            {busy === "copy" ? (
              <LoaderCircle className="dev-card-spinner" size={17} />
            ) : message.startsWith("Image copied") ? (
              <Check size={17} />
            ) : (
              <Copy size={17} />
            )}
            Copy image
          </button>
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
    </aside>
  );
}
