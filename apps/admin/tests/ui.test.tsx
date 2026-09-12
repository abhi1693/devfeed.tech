import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { LoginPanel } from "@/components/organisms/login-panel";
import { Metric } from "@/components/molecules/metric";
import { UserMenu } from "@/components/molecules/user-menu";

describe("simple admin UI", () => {
  it("uses document navigation for OIDC without local credential fields", () => {
    const html = renderToStaticMarkup(<LoginPanel enabled />);
    expect(html).toContain('href="/api/v1/admin/auth/login"');
    expect(html).toContain("Sign in with your organization");
    expect(html).not.toContain("<input");
  });
  it("does not offer login when configuration is missing", () => {
    const html = renderToStaticMarkup(<LoginPanel enabled={false} />);
    expect(html).not.toContain('href="/api/v1/admin/auth/login"');
    expect(html).toContain("Sign-in is not available");
  });
  it("renders errors accessibly and escapes content", () => {
    const html = renderToStaticMarkup(<LoginPanel enabled error="<script>bad</script>" />);
    expect(html).toContain('role="alert"');
    expect(html).not.toContain("<script>");
    expect(html).toContain('href="/api/v1/admin/auth/login?reauthenticate=true"');
    expect(html).toContain("Sign in again");
    expect(html).toContain("A fresh sign-in will be requested.");
  });
  it("does not replace genuine zero counts with mock data", () => {
    const html = renderToStaticMarkup(<Metric label="Topics" value={0} />);
    expect(html).toContain("Topics");
    expect(html).toContain(">0</p>");
  });
  it("offers an account menu without exposing internal identity or the csrf token in markup", () => {
    const html = renderToStaticMarkup(
      <UserMenu
        admin={{
          subject: "internal-subject",
          issuer: "https://identity.example",
          organization_id: "org",
          roles: ["superuser"],
          expires_at: 4102444800,
          csrf_token: "csrf-secret",
        }}
      />,
    );
    expect(html).toContain("User menu: Admin account");
    expect(html).toContain('type="button"');
    expect(html).toContain('aria-haspopup="menu"');
    expect(html).not.toContain("csrf-secret");
    expect(html).not.toContain("internal-subject");
  });
  it("keeps fresh sign-in after logout without a static success banner", () => {
    const html = renderToStaticMarkup(<LoginPanel enabled signedOut />);
    expect(html).not.toContain("You have signed out of DevFeed.");
    expect(html).toContain('href="/api/v1/admin/auth/login?reauthenticate=true"');
  });
  it("does not show a sign-out confirmation alongside an error", () => {
    const html = renderToStaticMarkup(<LoginPanel enabled signedOut error="Service unavailable" />);
    expect(html).not.toContain("You have signed out");
  });
});
