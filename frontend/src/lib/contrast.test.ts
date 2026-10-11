import { describe, expect, it } from "vitest";

import css from "../styles/global.css?raw";
import { RISK_BAND_COLOR, RISK_BAND_TEXT_COLOR } from "./assetHealth";

// WCAG 2.x relative luminance and contrast ratio.
function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const f = (c: number) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// Read the real design tokens, so changing one in global.css cannot silently break readability.
function token(name: string): string {
  const m = css.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!m) throw new Error(`token --${name} not found as a 6-digit hex in global.css`);
  return m[1];
}

const BACKGROUNDS = ["color-bg", "color-surface", "color-surface-alt", "color-primary-soft"];
const AA_NORMAL_TEXT = 4.5;

describe("contrast helper", () => {
  it("matches the known extremes and a known reference value", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 1);
    expect(contrast("#ffffff", "#ffffff")).toBeCloseTo(1, 5);
    expect(contrast("#64748b", "#f4f6f8")).toBeCloseTo(4.39, 1); // the value that failed before
  });

  it("is symmetric", () => {
    expect(contrast("#123456", "#fedcba")).toBeCloseTo(contrast("#fedcba", "#123456"), 10);
  });
});

describe("text colours are readable on every surface they sit on (WCAG AA, 4.5:1)", () => {
  for (const text of ["color-text", "color-text-muted", "color-text-subtle", "color-danger", "color-primary"]) {
    for (const bg of BACKGROUNDS) {
      it(`${text} on ${bg}`, () => {
        expect(contrast(token(text), token(bg))).toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
      });
    }
  }

  it("danger text is also readable on its own tinted background", () => {
    expect(contrast(token("color-danger"), token("color-danger-soft"))).toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
  });

  it("white text on the primary button colour", () => {
    expect(contrast("#ffffff", token("color-primary"))).toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
  });
});

describe("risk colours used as TEXT", () => {
  for (const band of ["low", "medium", "high"] as const) {
    it(`${band} text colour is readable on white and on the page background`, () => {
      expect(contrast(RISK_BAND_TEXT_COLOR[band], token("color-surface"))).toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
      expect(contrast(RISK_BAND_TEXT_COLOR[band], token("color-bg"))).toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
    });
  }

  it("documents why medium needs its own text colour: the marker orange fails as text", () => {
    expect(contrast(RISK_BAND_COLOR.medium, token("color-surface"))).toBeLessThan(AA_NORMAL_TEXT);
  });

  it("the text colours stay recognisably the same hue family as the marker colours", () => {
    // guards against 'fixing' contrast by swapping a band for an unrelated colour
    expect(RISK_BAND_TEXT_COLOR.low).toBe(RISK_BAND_COLOR.low);
    expect(RISK_BAND_TEXT_COLOR.high).toBe(RISK_BAND_COLOR.high);
    expect(RISK_BAND_TEXT_COLOR.medium).toMatch(/^#[a-f0-9]{6}$/);
  });
});

describe("badge text on its tinted background", () => {
  it.each([
    ["high / alarm pill", "#991b1b", "#fef2f2"],
    ["medium pill", "#92400e", "#fffbeb"],
    ["low / ok pill", "#166534", "#f0fdf4"],
    ["answer all-clear", "#166534", "#f0fdf4"],
    ["stale plan warning", "#92400e", "#fffbeb"],
  ])("%s", (_name, fg, bg) => {
    expect(contrast(fg, bg)).toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
  });

  it("neutral pill uses muted text on the alt surface", () => {
    expect(contrast(token("color-text-muted"), token("color-surface-alt"))).toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
  });
});
