"use client";
import type { ProfileEditorValue } from "@/lib/use-profile-editor";
import type { DevCardPreviewFormat } from "./dev-card-preview";
import { DevCardDesignEditor } from "./dev-card-design-editor";
import { DevCardContentEditor } from "./dev-card-content-editor";
export function ProfileCardFields({
  value,
  previewFormat,
  onChange: change,
}: {
  value: ProfileEditorValue;
  previewFormat: DevCardPreviewFormat;
  onChange: (value: ProfileEditorValue) => void;
}) {
  return (
    <div className="dev-card-customizer">
      <DevCardDesignEditor
        value={value.dev_card}
        showMotion={previewFormat !== "x-header"}
        onChange={(dev_card) => change({ ...value, dev_card })}
      />
      <DevCardContentEditor
        value={value.dev_card}
        stack={value.stack}
        onChange={(dev_card) => change({ ...value, dev_card })}
      />
    </div>
  );
}
