import axe from "axe-core";

// Accessibility checks that run on the DOM the tests already render.
//
// jsdom has no layout, so colour contrast and visibility rules cannot run here; contrast is checked
// separately (see lib/contrast.test.ts) and by auditing the real browser. What jsdom CAN catch, and
// what regressed before this existed: unlabeled controls, invalid ARIA, duplicate ids, bad roles, and
// a heading outline with no h1 or skipped levels.

export async function a11yViolations(container: Element): Promise<string[]> {
  const results = await axe.run(container, {
    resultTypes: ["violations"],
    rules: {
      "color-contrast": { enabled: false },
      // page-level rules need a whole document, which a rendered fragment is not
      region: { enabled: false },
      "landmark-one-main": { enabled: false },
      "page-has-heading-one": { enabled: false },
    },
  });
  return results.violations.map(
    (v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`
  );
}

// The page's heading levels in document order, e.g. [1, 2, 3, 2].
export function headingLevels(container: Element): number[] {
  return Array.from(container.querySelectorAll("h1, h2, h3, h4, h5, h6")).map((h) =>
    Number(h.tagName.slice(1))
  );
}

// A usable outline: exactly one h1, first, and no level skipped on the way down.
export function outlineProblems(levels: number[]): string[] {
  const problems: string[] = [];
  const h1s = levels.filter((l) => l === 1).length;
  if (h1s !== 1) problems.push(`expected exactly one h1, found ${h1s}`);
  if (levels.length > 0 && levels[0] !== 1) problems.push(`first heading is h${levels[0]}, not h1`);
  for (let i = 1; i < levels.length; i += 1) {
    if (levels[i] > levels[i - 1] + 1) {
      problems.push(`h${levels[i - 1]} is followed by h${levels[i]} (skips a level)`);
    }
  }
  return problems;
}
