import path from "node:path";
import { beforeEach, vi } from "vitest";

// The normal web test command runs in apps/web. Stryker starts workers at the monorepo root.
const webRoot = path.resolve(import.meta.dirname, "../../apps/web");
beforeEach(() => vi.spyOn(process, "cwd").mockReturnValue(webRoot));
