export function Skeleton({ width = "100%", height = 14 }: { width?: number | string; height?: number }): React.JSX.Element {
  return <span className="skeleton" aria-hidden="true" style={{ width, height }} />;
}

// Placeholder shaped like the loaded Results content (KPI tiles + table rows).
export function ResultsSkeleton(): React.JSX.Element {
  return (
    <div aria-busy="true" aria-label="Loading results" role="status">
      <div className="kpi-row" aria-hidden="true">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="kpi kpi-neutral">
            <Skeleton width="55%" height={11} />
            <div style={{ marginTop: 10 }}>
              <Skeleton width="40%" height={26} />
            </div>
          </div>
        ))}
      </div>
      <div aria-hidden="true" style={{ display: "grid", gap: 14 }}>
        {[0, 1, 2, 3, 4].map((i) => (
          <Skeleton key={i} height={18} />
        ))}
      </div>
    </div>
  );
}
