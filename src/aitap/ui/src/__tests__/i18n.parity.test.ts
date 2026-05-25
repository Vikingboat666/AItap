/**
 * i18n locale parity — guards the project rule that EVERY user-facing
 * string ships in BOTH English and 简体中文.
 *
 * Any PR that adds a key to one locale but forgets the other (or leaves a
 * value blank) fails here, so "中英文两个版本" can't silently drift out of
 * sync. This runs in the normal test gate and in CI.
 *
 * See CLAUDE.md → "Internationalization (i18n)" for the convention this
 * test enforces.
 */
import { describe, expect, it } from "vitest";

import en from "../i18n/en.json";
import zh from "../i18n/zh.json";

type Json = string | { [k: string]: Json };

/** Flatten a nested locale object into dotted leaf keys. */
function leafKeys(obj: Json, prefix = ""): string[] {
  if (typeof obj === "string") return [prefix];
  return Object.entries(obj).flatMap(([k, v]) =>
    leafKeys(v, prefix ? `${prefix}.${k}` : k),
  );
}

/** Collect dotted keys whose value is an empty / whitespace-only string. */
function blankValueKeys(obj: Json, prefix = ""): string[] {
  if (typeof obj === "string") return obj.trim().length === 0 ? [prefix] : [];
  return Object.entries(obj).flatMap(([k, v]) =>
    blankValueKeys(v, prefix ? `${prefix}.${k}` : k),
  );
}

describe("i18n locale parity (en ⇄ zh)", () => {
  const enKeys = leafKeys(en as Json).sort();
  const zhKeys = leafKeys(zh as Json).sort();

  it("en and zh expose the exact same set of keys", () => {
    const missingInZh = enKeys.filter((k) => !zhKeys.includes(k));
    const missingInEn = zhKeys.filter((k) => !enKeys.includes(k));
    expect(
      missingInZh,
      `keys present in en.json but missing from zh.json:\n${missingInZh.join("\n")}`,
    ).toEqual([]);
    expect(
      missingInEn,
      `keys present in zh.json but missing from en.json:\n${missingInEn.join("\n")}`,
    ).toEqual([]);
  });

  it("no locale has blank values", () => {
    expect(blankValueKeys(en as Json), "blank values in en.json").toEqual([]);
    expect(blankValueKeys(zh as Json), "blank values in zh.json").toEqual([]);
  });
});
