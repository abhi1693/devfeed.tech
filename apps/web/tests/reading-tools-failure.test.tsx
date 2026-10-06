// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReadingTools } from "@/components/reading-tools";

vi.mock("@/components/user-account", () => ({ useUser: () => ({ user: { user_id: "reader" } }) }));
vi.mock("@/components/account-reading-tools", () => {
  throw new Error("Reading tools chunk download failed");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("keeps the reader usable when the optional tools chunk cannot download", async () => {
  const errors = vi.spyOn(console, "error").mockImplementation(() => {});
  render(
    <>
      <header>
        <ReadingTools />
        <button>Account</button>
      </header>
      <main>Reader articles</main>
    </>,
  );
  await waitFor(() => expect(errors).toHaveBeenCalled());
  expect(screen.getByRole("main").textContent).toBe("Reader articles");
  expect(screen.getByRole("button", { name: "Account" })).toBeTruthy();
});
