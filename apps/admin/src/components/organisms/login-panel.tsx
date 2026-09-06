import { Button } from "@/components/atoms/button";
import { Alert, AlertDescription } from "@/components/atoms/alert";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/atoms/card";
import { LoginFeedback } from "@/components/molecules/login-feedback";

export function LoginPanel({ enabled, error, signedOut = false }: { enabled: boolean; error?: string; signedOut?: boolean }) {
  const freshSignIn = Boolean(error) || signedOut;
  return <Card className="w-full max-w-sm shadow-none">
    <LoginFeedback error={error} signedOut={signedOut} />
    <CardHeader>
      <CardTitle className="text-xl">Admin sign-in</CardTitle>
      <CardDescription>Manage DevFeed and its data.</CardDescription>
    </CardHeader>
    <CardContent className="space-y-5">
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      {enabled ? <Button asChild className="w-full">
        {/* OAuth requires document navigation, not Next Link prefetch/client navigation. */}
        <a href={freshSignIn ? "/api/v1/admin/auth/login?reauthenticate=true" : "/api/v1/admin/auth/login"}>{error ? "Sign in again" : "Sign in with your organization"}</a>
      </Button> : <p className="text-sm text-muted-foreground">
        Sign-in is not available. Configure the admin service’s OIDC settings.
      </p>}
      {enabled && error && <p className="text-xs text-muted-foreground">Try again after your access is updated, or use another account. A fresh sign-in will be requested.</p>}
      <p className="text-xs text-muted-foreground">Access requires the administrator role in your organization. No public registration.</p>
    </CardContent>
  </Card>;
}
