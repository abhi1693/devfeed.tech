import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { bindReaderOperation, rejectReaderMethod } from "@/lib/server/gateway";
import { userGatewayOperations } from "@/lib/api/generated/user-operations";

vi.mock("@devfeed/telemetry/propagation", () => ({ traceHeaders: () => ({}) }));

const origin = "https://user.example";

beforeEach(() => {
  vi.stubEnv("DEVFEED_USER_BASE_URL", origin);
  vi.stubEnv("DEVFEED_USER_API_URL", "http://user-api:8002");
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it("requires an explicit placement validator when binding an operation", async () => {
  const operation = userGatewayOperations["placements_v1_user_partner_placements_query_post"];
  expect(() => bindReaderOperation(operation)).toThrow(
    "Placement operations require their dedicated handler",
  );
  expect(() => bindReaderOperation(userGatewayOperations.user_auth_me)).not.toThrow();

  const validator = vi.fn<(body: ArrayBuffer) => Response>(() =>
    Response.json({ detail: "Invalid placement context" }, { status: 422 }),
  );
  const handler = bindReaderOperation(operation, validator);
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  const body = '{"surface":"article"}';
  const response = await handler(
    new Request(`${origin}/api`, {
      method: "POST",
      headers: { Origin: origin },
      body,
    }),
    {},
  );
  expect(response.status).toBe(422);
  expect(await response.json()).toEqual({ detail: "Invalid placement context" });
  expect(validator).toHaveBeenCalledOnce();
  expect(new TextDecoder().decode(validator.mock.calls[0][0])).toBe(body);
  expect(fetcher).not.toHaveBeenCalled();
});

it.each([["GET"], ["GET", "POST"], ["PUT", "DELETE"]])(
  "advertises exactly the permitted methods %j for rejected requests",
  (...methods) => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const handler = rejectReaderMethod(methods);
    const response = handler();
    expect(response.status).toBe(405);
    expect(response.headers.get("allow")).toBe(methods.join(", "));
    expect(fetcher).not.toHaveBeenCalled();
  },
);
