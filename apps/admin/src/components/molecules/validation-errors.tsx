import { Alert, AlertDescription } from "@/components/atoms/alert";
import { ApiError } from "@/lib/api/client";

/** Keep validation without a dedicated field (including nested assignments) actionable. */
export function ValidationErrors({
  error,
  inlineFields = [],
}: {
  error?: Error;
  inlineFields?: string[];
}) {
  const fields =
    error instanceof ApiError
      ? Object.entries(error.fields).filter(([key]) => !inlineFields.includes(key))
      : [];
  if (!fields.length) return null;
  return (
    <Alert variant="destructive">
      <AlertDescription>
        <p>Please correct the following fields:</p>
        <ul className="list-inside list-disc">
          {fields.map(([key, message]) => (
            <li key={key}>
              {key ? `${key}: ` : ""}
              {message}
            </li>
          ))}
        </ul>
      </AlertDescription>
    </Alert>
  );
}
