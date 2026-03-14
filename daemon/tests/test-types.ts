import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { modeToCliFlag, PermissionMode } from "../src/types";

describe("modeToCliFlag()", () => {
  it('returns ["--dangerously-skip-permissions"] for "auto" mode', () => {
    const flags = modeToCliFlag("auto");
    assert.deepEqual(flags, ["--dangerously-skip-permissions"]);
  });

  it('returns [] for "code" mode', () => {
    const flags = modeToCliFlag("code");
    assert.deepEqual(flags, []);
  });

  it('returns [] for "plan" mode', () => {
    const flags = modeToCliFlag("plan");
    assert.deepEqual(flags, []);
  });

  it('returns [] for "ask" mode', () => {
    const flags = modeToCliFlag("ask");
    assert.deepEqual(flags, []);
  });

  it('returns ["--dangerously-skip-permissions"] for unknown/default mode', () => {
    // Cast to PermissionMode to test default branch
    const flags = modeToCliFlag("unknown" as PermissionMode);
    assert.deepEqual(flags, ["--dangerously-skip-permissions"]);
  });

  it("returns an array (not undefined or null) for all valid modes", () => {
    const modes: PermissionMode[] = ["auto", "code", "plan", "ask"];
    for (const mode of modes) {
      const result = modeToCliFlag(mode);
      assert.ok(Array.isArray(result), `Expected array for mode "${mode}"`);
    }
  });

  it('"auto" flag array has exactly one element', () => {
    const flags = modeToCliFlag("auto");
    assert.equal(flags.length, 1);
  });

  it("non-auto modes return empty arrays", () => {
    const nonAutoModes: PermissionMode[] = ["code", "plan", "ask"];
    for (const mode of nonAutoModes) {
      const flags = modeToCliFlag(mode);
      assert.equal(flags.length, 0, `Expected empty array for mode "${mode}"`);
    }
  });
});
