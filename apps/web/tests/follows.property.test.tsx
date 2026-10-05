// @vitest-environment jsdom
import fc from "fast-check";
import { act, cleanup, renderHook } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { useQueryClient } from "@tanstack/react-query";
import { ReaderQueryProvider } from "@/components/reader-query-provider";
import { useFollowPreferences } from "@/lib/use-follow-preferences";
import { AccountError, userRequest, type UserIdentity } from "@/lib/user";
import { commandOptions, propertyOptions } from "../../../scripts/ci/property-config.mjs";

const account = vi.hoisted(() => ({
  user: null as UserIdentity | null,
  loading: false,
  sessionRevision: 1,
}));
vi.mock("@/components/user-account", () => ({ useUser: () => account }));
vi.mock("@/lib/user", async (original) => ({
  ...(await original<typeof import("@/lib/user")>()),
  userRequest: vi.fn(),
}));

type Kind = "source" | "topic";
const id = fc.integer({ min: 0, max: 7 }).map((value) => `id-${value}`);
const ids = fc.uniqueArray(id, { maxLength: 8 });
const choices = ["alice", "bob", null] as const;
type Owner = (typeof choices)[number];
type Action =
  | { type: "toggle"; id: string; fail: boolean }
  | { type: "save"; ids: string[] }
  | { type: "refresh"; ids: string[] }
  | { type: "account"; owner: Owner }
  | { type: "session" };
type Model = { owner: Owner; data: Record<string, Set<string>>; writes: number };

function identify(owner: Owner) {
  account.user =
    owner === null
      ? null
      : {
          user_id: owner,
          csrf_token: `csrf-${owner}-${account.sessionRevision}`,
          name: null,
          email: null,
          expires_at: 4102444800,
        };
}

// TanStack Query schedules observer notifications after the request microtask.
async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function mount(kind: Kind, initial: string[]) {
  account.sessionRevision = 1;
  identify("alice");
  const server: Record<string, Set<string>> = {
    alice: new Set(initial),
    bob: new Set(["bob-only"]),
  };
  const control = { fail: false, writes: 0, guestRequests: 0 };
  vi.mocked(userRequest)
    .mockReset()
    .mockImplementation(async (path, init) => {
      if (!account.user) {
        control.guestRequests++;
        throw new Error("Guest request");
      }
      const owner = account.user!.user_id;
      const field = `${kind}_ids`;
      if (init?.method !== "PUT") return { [field]: [...server[owner]] };
      control.writes++;
      expect(init.headers).toMatchObject({ "X-CSRF-Token": account.user!.csrf_token });
      if (control.fail) {
        control.fail = false;
        throw new AccountError(503);
      }
      const body = JSON.parse(String(init.body));
      if (typeof body.followed === "boolean") {
        const entry = path.split("/").at(-1)!;
        if (body.followed) server[owner].add(entry);
        else server[owner].delete(entry);
        return { followed: body.followed };
      }
      server[owner] = new Set(body[field]);
      return { [field]: [...server[owner]] };
    });
  const view = renderHook(
    () => ({
      first: useFollowPreferences(kind),
      second: useFollowPreferences(kind),
      client: useQueryClient(),
    }),
    { wrapper: ReaderQueryProvider },
  );
  return { view, server, control };
}
type Real = ReturnType<typeof mount>;

function checkCache(model: Model, real: Real) {
  const expected = model.owner === null ? [] : [...model.data[model.owner]].sort();
  for (const consumer of [real.view.result.current.first, real.view.result.current.second]) {
    expect([...consumer.ids].sort()).toEqual(expected);
    expect(new Set(consumer.ids).size).toBe(consumer.ids.length);
    expect(consumer.busy).toEqual([]);
    expect(consumer.unavailable).toBe(false);
  }
  expect(real.control.writes).toBe(model.writes);
  expect(real.control.guestRequests).toBe(0);
}

class FollowCommand implements fc.AsyncCommand<Model, Real> {
  constructor(readonly action: Action) {}
  check() {
    return true;
  }
  toString() {
    return JSON.stringify(this.action);
  }
  async run(model: Model, real: Real) {
    const action = this.action;
    if (action.type === "account" || action.type === "session") {
      if (action.type === "account") model.owner = action.owner;
      else account.sessionRevision++;
      identify(model.owner);
      real.view.rerender();
    } else if (action.type === "refresh") {
      if (model.owner !== null) {
        real.server[model.owner] = new Set(action.ids);
        model.data[model.owner] = new Set(action.ids);
      }
      await act(async () => real.view.result.current.second.refresh());
    } else {
      real.control.fail = action.type === "toggle" && action.fail;
      await act(async () => {
        const consumer = real.view.result.current.first;
        if (action.type === "toggle") await consumer.toggle(action.id);
        else {
          const saved = await consumer.save(action.ids);
          expect(saved).toEqual(model.owner === null ? null : action.ids);
        }
      });
      if (model.owner !== null) {
        model.writes++;
        if (action.type === "save") model.data[model.owner] = new Set(action.ids);
        else if (!action.fail) {
          const followed = model.data[model.owner];
          if (followed.has(action.id)) followed.delete(action.id);
          else followed.add(action.id);
        }
      }
      real.control.fail = false;
    }
    await flush();
    checkCache(model, real);
  }
}

const actions: fc.Arbitrary<Action>[] = [
  fc.record({ type: fc.constant("toggle" as const), id, fail: fc.boolean() }),
  fc.record({ type: fc.constant("save" as const), ids }),
  fc.record({ type: fc.constant("refresh" as const), ids }),
  fc.record({ type: fc.constant("account" as const), owner: fc.constantFrom(...choices) }),
  fc.constant({ type: "session" as const }),
];

for (const kind of ["source", "topic"] as const) {
  it(`${kind} cache matches the account model across arbitrary follow, save, failure and refresh actions`, async () => {
    await fc.assert(
      fc.asyncProperty(
        ids,
        fc.commands(
          actions.map((arbitrary) => arbitrary.map((action) => new FollowCommand(action))),
          commandOptions(),
        ),
        async (initial, commands) => {
          const real = mount(kind, initial);
          const model: Model = {
            owner: "alice",
            data: { alice: new Set(initial), bob: new Set(["bob-only"]) },
            writes: 0,
          };
          try {
            await flush();
            checkCache(model, real);
            await fc.asyncModelRun(() => ({ model, real }), commands);
          } finally {
            const client = real.view.result.current.client;
            cleanup();
            expect(client.getQueryCache().getAll()).toHaveLength(0);
            expect(client.getMutationCache().getAll()).toHaveLength(0);
          }
        },
      ),
      propertyOptions(true),
    );
  }, 300_000);

  it(`${kind} cache merges reordered acknowledgements and rejects duplicate or conflicting writes`, async () => {
    await fc.assert(
      fc.asyncProperty(
        ids,
        fc.uniqueArray(id, { minLength: 2, maxLength: 2 }),
        fc.boolean(),
        fc.boolean(),
        async (initial, [first, second], reversed, changeAccount) => {
          const real = mount(kind, initial);
          const pending: Array<{
            resolve: (value: { followed: boolean }) => void;
            signal: AbortSignal;
          }> = [];
          try {
            await flush();
            let resolveRead!: (value: Record<string, string[]>) => void;
            let readSignal: AbortSignal | undefined;
            vi.mocked(userRequest).mockImplementation((_path, init) => {
              if (init?.method !== "PUT" && readSignal === undefined)
                return new Promise((resolve) => {
                  readSignal = init?.signal ?? undefined;
                  resolveRead = resolve;
                });
              if (init?.method !== "PUT")
                return Promise.resolve({ [`${kind}_ids`]: ["fresh-session"] });
              return new Promise((resolve) => pending.push({ resolve, signal: init.signal! }));
            });
            let a!: Promise<void>;
            let b!: Promise<void>;
            await act(async () => real.view.result.current.first.refresh());
            expect(readSignal).toBeDefined();
            await act(async () => {
              a = real.view.result.current.first.toggle(first);
              b = real.view.result.current.second.toggle(second);
              await real.view.result.current.second.toggle(first);
              expect(await real.view.result.current.first.save(["conflicting-bulk"])).toBeNull();
              real.view.result.current.second.refresh();
            });
            expect(pending).toHaveLength(2);
            expect(readSignal!.aborted).toBe(true);
            if (changeAccount) {
              account.sessionRevision++;
              identify("bob");
              real.view.rerender();
              await flush();
              expect(pending.every((write) => write.signal.aborted)).toBe(true);
            }
            for (const index of reversed ? [1, 0] : [0, 1]) {
              await act(async () => pending[index].resolve({ followed: true }));
            }
            await act(async () => {
              await Promise.all([a, b]);
              resolveRead({ [`${kind}_ids`]: initial });
            });
            await flush();
            const expected = changeAccount
              ? ["fresh-session"]
              : [...new Set([...initial, first, second])].sort();
            expect([...real.view.result.current.first.ids].sort()).toEqual(expected);
            expect([...real.view.result.current.second.ids].sort()).toEqual(expected);
            // Two initial consumers share one read; blocked actions issue no requests.
            expect(userRequest).toHaveBeenCalledTimes(changeAccount ? 5 : 4);
          } finally {
            cleanup();
          }
        },
      ),
      propertyOptions(true),
    );
  }, 300_000);
}
