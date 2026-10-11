import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ResultsRedirect } from "./ResultsRedirect";

function Where(): React.JSX.Element {
  const { pathname, search } = useLocation();
  return <div data-testid="where">{pathname + search}</div>;
}

function renderAt(url: string) {
  render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/facilities/:facilityId/results" element={<ResultsRedirect />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("ResultsRedirect", () => {
  it("sends an old Results link to the Overview", () => {
    renderAt("/facilities/f1/results");
    expect(screen.getByTestId("where")).toHaveTextContent("/facilities/f1/overview");
  });

  it("sends an old link that carried a floor filter to the Assets tab, keeping the query", () => {
    renderAt("/facilities/f1/results?floor=2&upload=u9");
    expect(screen.getByTestId("where")).toHaveTextContent("/facilities/f1/assets?floor=2&upload=u9");
  });

  it("ignores other query strings (the old map-back `upload` param)", () => {
    renderAt("/facilities/f1/results?upload=u9");
    expect(screen.getByTestId("where")).toHaveTextContent("/facilities/f1/overview");
  });
});
