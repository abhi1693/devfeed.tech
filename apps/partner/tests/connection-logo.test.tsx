// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import {
  ConnectionLogo,
  ConnectionLogoField,
  type ConnectionLogoChange,
  type ConnectionLogoState,
} from "@devfeed/ui/connection-logo";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("previews managed variants without fetching the editable remote source", () => {
  const view = render(
    <ConnectionLogoField
      name="Launch platform"
      value="https://remote.example/private-source.png"
      file={null}
      savedUrl="https://cdn.example/logo-64.webp"
      variants={[{ url: "https://cdn.example/logo-32.webp", width: 32 }]}
      onChange={vi.fn()}
    />,
  );
  const image = view.container.querySelector("img")!;
  expect(image.getAttribute("src")).toBe("https://cdn.example/logo-64.webp");
  expect(image.getAttribute("srcset")).toBe("https://cdn.example/logo-32.webp 32w");
  expect(view.container.querySelector('img[src*="remote.example"]')).toBeNull();
  fireEvent.error(image);
  expect(view.container.querySelector("img")).toBeNull();
  expect(screen.getByText("LA")).toBeTruthy();
});

it("stages one valid file, supports replacing it with a URL, and removes it without submitting", () => {
  const submit = vi.fn();
  function Form() {
    const [value, setValue] = useState("");
    const [file, setFile] = useState<File | null>(null);
    return (
      <form onSubmit={submit}>
        <ConnectionLogoField
          name="Catalog"
          value={value}
          file={file}
          onChange={(url, next) => {
            setValue(url);
            setFile(next);
          }}
        />
      </form>
    );
  }
  render(<Form />);
  const file = new File(["image"], "platform.png", { type: "image/png" });
  fireEvent.change(screen.getByLabelText("Logo image file"), { target: { files: [file] } });
  expect(screen.getByText("platform.png")).toBeTruthy();
  fireEvent.change(screen.getByRole("textbox", { name: "Logo URL" }), {
    target: { value: "https://other.example/logo.webp" },
  });
  expect(screen.queryByRole("status")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Remove logo" }));
  expect((screen.getByRole("textbox", { name: "Logo URL" }) as HTMLInputElement).value).toBe("");
  expect(submit).not.toHaveBeenCalled();
});

it("previews the selected file locally and releases it when replaced or removed", () => {
  const original = URL;
  const create = vi
    .fn()
    .mockReturnValueOnce("blob:preview-one")
    .mockReturnValueOnce("blob:preview-two");
  const revoke = vi.fn();
  vi.stubGlobal(
    "URL",
    class extends original {
      static createObjectURL = create;
      static revokeObjectURL = revoke;
    },
  );
  function Field() {
    const [file, setFile] = useState<File | null>(null);
    return (
      <ConnectionLogoField
        name="Platform"
        value=""
        file={file}
        onChange={(_url, next) => setFile(next)}
      />
    );
  }
  const view = render(<Field />);
  fireEvent.change(screen.getByLabelText("Logo image file"), {
    target: { files: [new File(["one"], "one.png", { type: "image/png" })] },
  });
  expect(
    screen
      .getByRole("img", { name: "Platform logo preview" })
      .querySelector("img")!
      .getAttribute("src"),
  ).toBe("blob:preview-one");
  fireEvent.change(screen.getByLabelText("Logo image file"), {
    target: { files: [new File(["two"], "two.png", { type: "image/png" })] },
  });
  expect(revoke).toHaveBeenCalledWith("blob:preview-one");
  fireEvent.click(screen.getByRole("button", { name: "Remove logo" }));
  expect(revoke).toHaveBeenCalledWith("blob:preview-two");
  expect(view.container.querySelector('img[src^="blob:"]')).toBeNull();
});

it.each([
  new File([], "empty.png", { type: "image/png" }),
  new File([new Uint8Array(5 * 1024 * 1024 + 1)], "large.png", { type: "image/png" }),
  new File(["<svg/>"], "vector.svg", { type: "image/svg+xml" }),
])("rejects unsupported upload $name before changing the draft", (file) => {
  const change = vi.fn();
  render(<ConnectionLogoField name="Catalog" value="" file={null} onChange={change} />);
  fireEvent.change(screen.getByLabelText("Logo image file"), { target: { files: [file] } });
  expect(screen.getByRole("alert")).toBeTruthy();
  expect(change).not.toHaveBeenCalled();
});

it("does not display insecure, credentialed or executable presentation URLs", () => {
  const view = render(<ConnectionLogo name="Platform" url="http://cdn.example/logo.png" />);
  for (const url of ["https://user:secret@cdn.example/logo.png", "javascript:alert(1)"]) {
    view.rerender(<ConnectionLogo name="Platform" url={url} />);
    expect(view.container.querySelector("img")).toBeNull();
  }
});

function immediateForm() {
  const remote = "https://remote.example/new.png";
  const stored = "https://cdn.example/current.webp";
  const canonical = "https://cdn.example/new.webp";
  const submit = vi.fn();
  const commit = vi
    .fn<(change: ConnectionLogoChange, signal: AbortSignal) => Promise<ConnectionLogoState>>()
    .mockImplementation(async (change) => ({
      logo_url: "file" in change || change.logo_url ? canonical : null,
    }));
  function Form() {
    const [value, setValue] = useState(stored);
    const [saved, setSaved] = useState<string | null>(stored);
    const [file, setFile] = useState<File | null>(null);
    const [busy, setBusy] = useState(false);
    return (
      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit({ logo_url: value, name: "Unsaved name" });
        }}
      >
        <ConnectionLogoField
          name="Platform"
          value={value}
          file={file}
          savedUrl={saved}
          onBusyChange={setBusy}
          onChange={(url, next) => {
            setValue(url);
            setFile(next);
          }}
          onCommit={commit}
          onSaved={(next) => {
            setValue(next.logo_url ?? "");
            setSaved(next.logo_url ?? null);
            setFile(null);
          }}
        />
        <button type="button">Other field</button>
        <button type="submit" disabled={busy}>
          Save form
        </button>
      </form>
    );
  }
  render(<Form />);
  return { remote, canonical, submit, commit };
}

it.each(["upload", "remove", "save"])(
  "uses the first %s click after editing a URL without starting a competing import",
  async (action) => {
    const { remote, canonical, submit, commit } = immediateForm();
    const user = userEvent.setup();
    const input = screen.getByRole("textbox", { name: "Logo URL" }) as HTMLInputElement;
    await user.clear(input);
    await user.type(input, remote);
    if (action === "upload") {
      const fileInput = screen.getByLabelText("Logo image file") as HTMLInputElement;
      const choose = vi.spyOn(fileInput, "click");
      await user.click(screen.getByRole("button", { name: "Upload logo" }));
      expect(choose).toHaveBeenCalledOnce();
      expect(commit).not.toHaveBeenCalled();
      const file = new File(["image"], "new.png", { type: "image/png" });
      fireEvent.change(fileInput, { target: { files: [file] } });
      await waitFor(() => expect(input.value).toBe(canonical));
      expect(commit).toHaveBeenCalledExactlyOnceWith({ file }, expect.any(AbortSignal));
    } else if (action === "remove") {
      await user.click(screen.getByRole("button", { name: "Remove logo" }));
      await waitFor(() => expect(input.value).toBe(""));
      expect(commit).toHaveBeenCalledExactlyOnceWith({ logo_url: null }, expect.any(AbortSignal));
    } else {
      await user.click(screen.getByRole("button", { name: "Save form" }));
      expect(submit).toHaveBeenCalledExactlyOnceWith({ logo_url: remote, name: "Unsaved name" });
      expect(commit).not.toHaveBeenCalled();
    }
  },
);

it.each(["outside click", "Enter", "keyboard through actions"])(
  "commits a pending URL on %s",
  async (action) => {
    const { remote, canonical, submit, commit } = immediateForm();
    const user = userEvent.setup();
    const input = screen.getByRole("textbox", { name: "Logo URL" }) as HTMLInputElement;
    await user.clear(input);
    await user.type(input, remote);
    if (action === "Enter") await user.keyboard("{Enter}");
    else if (action === "outside click")
      await user.click(screen.getByRole("button", { name: "Other field" }));
    else {
      await user.tab();
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "Upload logo" }));
      expect(commit).not.toHaveBeenCalled();
      await user.tab();
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "Remove logo" }));
      await user.tab();
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "Other field" }));
    }
    await waitFor(() => expect(input.value).toBe(canonical));
    expect(commit).toHaveBeenCalledExactlyOnceWith({ logo_url: remote }, expect.any(AbortSignal));
    expect(submit).not.toHaveBeenCalled();
  },
);
