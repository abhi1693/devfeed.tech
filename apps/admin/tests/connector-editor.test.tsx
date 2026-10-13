// @vitest-environment jsdom

import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConnectorFields } from "@devfeed/ui/connector-fields";
import { ConnectorResponse } from "@devfeed/ui/connector-response";
import { responseMappings } from "@devfeed/ui/connector-response-mappings";
import { connectorDefaults } from "@/lib/partner-connectors";
import type { ConnectorConfig } from "@/lib/api/generated/models";

afterEach(cleanup);

describe("shared connector editor", () => {
  it("pairs colored check feedback with the same response paths and treats unmatched samples as hints", () => {
    const config = connectorDefaults("shipyardhq");
    const data = { items: [{ id: "one", name: "One" }], nextCursor: null };
    const mappings = responseMappings(config, data);
    render(
      <>
        <ConnectorFields
          value={config}
          defaults={connectorDefaults()}
          onChange={vi.fn()}
          disabled={false}
          mappings={mappings}
        />
        <ConnectorResponse ready data={data} sampled mappings={mappings} onRefresh={vi.fn()} />
      </>,
    );
    const found = screen.getByLabelText("Product name found in sample");
    expect(found.querySelector("svg.lucide-check")).not.toBeNull();
    expect(found.getAttribute("data-mapping-status")).toBe("matched");
    const input = screen.getByRole("textbox", { name: "Product name paths" });
    const json = screen.getByRole("textbox", { name: "API response JSON" });
    const mapped = json.querySelector('[data-mappings="fields.name"]');
    expect(input.className).toContain("border-emerald-600");
    expect(mapped?.className).toContain("border-emerald-600");
    expect(mapped?.textContent).toContain('"name": "One"');
    expect(
      screen
        .getByLabelText("Description not found in this sample")
        .getAttribute("data-mapping-status"),
    ).toBe("unmatched");
    expect(
      screen.getByRole("textbox", { name: "Description paths" }).getAttribute("aria-invalid"),
    ).toBe("false");
    expect(screen.getByText(/Sample response; long lists/)).not.toBeNull();
  });

  it("uses caller-owned authentication without exposing environment controls and applies partner bounds", () => {
    render(
      <ConnectorFields
        value={connectorDefaults("shipyardhq")}
        defaults={connectorDefaults()}
        onChange={vi.fn()}
        disabled={false}
        authentication={
          <label>
            Private credential
            <input type="password" />
          </label>
        }
        limits={{
          max_pages: 100,
          requests_per_minute: 60,
          timeout_seconds: 15,
          max_response_bytes: 2000000,
        }}
      />,
    );
    const credential = screen.getByLabelText("Private credential");
    expect(credential.closest("details")?.open).toBe(true);
    expect(screen.queryByRole("textbox", { name: "Secret reference" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Authentication" })).toBeNull();
    for (const [label, max] of [
      ["Maximum pages", "100"],
      ["Requests per minute", "60"],
      ["Timeout (seconds)", "15"],
      ["Maximum response (bytes)", "2000000"],
    ]) {
      expect(screen.getByRole("spinbutton", { name: label }).getAttribute("max")).toBe(max);
    }
    expect(screen.queryByText(/found in sample/)).toBeNull();
  });

  it("preserves comma typing while updating parsed fallback paths", async () => {
    const changed = vi.fn();
    function Editor() {
      const [value, setValue] = useState<ConnectorConfig>({
        ...connectorDefaults(),
        fields: { name: ["name"] },
      });
      return (
        <ConnectorFields
          value={value}
          defaults={connectorDefaults()}
          disabled={false}
          onChange={(next) => {
            setValue(next);
            changed(next);
          }}
        />
      );
    }
    render(<Editor />);
    const input = screen.getByRole("textbox", { name: "Product name paths" }) as HTMLInputElement;
    const user = userEvent.setup();
    await user.type(input, ", title");
    expect(input.value).toBe("name, title");
    expect(changed.mock.lastCall?.[0].fields.name).toEqual(["name", "title"]);
  });

  it("preserves server validation and accessible shared filter controls", () => {
    const changed = vi.fn();
    render(
      <ConnectorFields
        value={connectorDefaults("nick-launches")}
        defaults={connectorDefaults()}
        disabled={false}
        onChange={changed}
        errors={{ "connector.fields.name.0": "Use a valid field path." }}
      />,
    );
    const input = screen.getByRole("textbox", { name: "Product name paths" });
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(
      document.getElementById(input.getAttribute("aria-describedby")!.split(" ").at(-1)!)
        ?.textContent,
    ).toBe("Use a valid field path.");
    const includeMissing = screen.getByRole("checkbox", {
      name: "Filter 1: include missing values",
    });
    fireEvent.click(includeMissing);
    expect(changed.mock.lastCall?.[0].filters[0].include_missing).toBe(false);
  });

  it("suggests a pagination parameter while preserving a custom parameter", async () => {
    function Editor() {
      const [value, setValue] = useState<ConnectorConfig>(connectorDefaults());
      return (
        <ConnectorFields
          suggestPaginationParameters
          value={value}
          defaults={connectorDefaults()}
          disabled={false}
          onChange={setValue}
        />
      );
    }
    render(<Editor />);
    const user = userEvent.setup();
    async function choose(label: string) {
      await user.click(screen.getByRole("combobox", { name: "Pagination mode" }));
      await user.click(screen.getByRole("option", { name: label }));
    }
    await choose("Cursor");
    const parameter = screen.getByRole("textbox", {
      name: "Pagination parameter",
    }) as HTMLInputElement;
    expect(parameter.value).toBe("cursor");
    fireEvent.change(parameter, { target: { value: "after" } });
    await choose("Page number");
    expect(parameter.value).toBe("after");
    await choose("None");
    expect(screen.queryByRole("textbox", { name: "Pagination parameter" })).toBeNull();
    await choose("Offset");
    expect(
      (
        screen.getByRole("textbox", {
          name: "Pagination parameter",
        }) as HTMLInputElement
      ).value,
    ).toBe("offset");
  });

  it("shows application-specific preview blockers and preserves JSON scalar responses", () => {
    const refresh = vi.fn();
    const { rerender } = render(
      <ConnectorResponse
        ready={false}
        status={<p>Select an account first.</p>}
        onRefresh={refresh}
      />,
    );
    expect(screen.getByText("Select an account first.")).not.toBeNull();
    expect(screen.queryByText(/Enter the API base URL/)).toBeNull();
    expect((screen.getByRole("button", { name: "Refresh" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    rerender(<ConnectorResponse ready data={null} onRefresh={refresh} />);
    expect(screen.getByRole("textbox", { name: "API response JSON" }).textContent?.trim()).toBe(
      "null",
    );
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(refresh).toHaveBeenCalledOnce();
  });
});
