import { assetRowButtonId, formatRul, RISK_BAND_COLOR, riskBand, type AssetHealthRow } from "../lib/assetHealth";

interface AssetHealthTableProps {
  rows: AssetHealthRow[];
  selectedAssetId: string | null;
  onSelect: (assetId: string) => void;
}

function VibrationCell({ row }: { row: AssetHealthRow }): React.JSX.Element {
  if (row.vibration === null) {
    return (
      <span
        className="pill pill-neutral"
        title={row.vibrationUnavailableReason ?? "No vibration data for this asset"}
      >
        no data
      </span>
    );
  }
  if (row.vibration.alarm) {
    return (
      <span className="pill pill-alarm">
        ALARM
        {row.vibration.alarmChannels !== null && row.vibration.channelsScored !== null
          ? ` (${row.vibration.alarmChannels}/${row.vibration.channelsScored} ch)`
          : ""}
      </span>
    );
  }
  return <span className="pill pill-ok">normal</span>;
}

export function AssetHealthTable({
  rows,
  selectedAssetId,
  onSelect,
}: AssetHealthTableProps): React.JSX.Element {
  if (rows.length === 0) {
    return <p style={{ fontSize: 13 }}>This facility has no assets yet.</p>;
  }

  return (
    <div className="table-scroll">
    <table className="data-table" aria-label="Asset health">
      <thead>
        <tr>
          <th>Asset</th>
          <th>Type</th>
          <th>Risk (0-100)</th>
          <th>Remaining life (cycles)</th>
          <th>Vibration</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const band = row.risk === null ? null : riskBand(row.risk);
          return (
            <tr
              key={row.asset.id}
              className={`row-clickable ${row.asset.id === selectedAssetId ? "row-selected" : ""}`}
              onClick={() => onSelect(row.asset.id)}
              aria-current={row.asset.id === selectedAssetId ? "true" : undefined}
            >
              <td>
                <button
                  type="button"
                  id={assetRowButtonId(row.asset.id)}
                  className="row-button"
                  onClick={(event) => {
                    event.stopPropagation();
                    onSelect(row.asset.id);
                  }}
                >
                  {row.asset.name}
                </button>
              </td>
              <td>{row.asset.type}</td>
              <td>
                {row.risk === null || band === null ? (
                  <span
                    className="pill pill-neutral"
                    title="No risk score: the risk model needs the asset's five AI4I sensors in one coherent snapshot"
                  >
                    not scored
                  </span>
                ) : (
                  <div className="risk-cell">
                    <span className="risk-number" style={{ color: RISK_BAND_COLOR[band] }}>
                      {row.risk.toFixed(1)}
                    </span>
                    <span className="risk-bar" aria-hidden="true">
                      <span
                        style={{
                          width: `${Math.max(2, Math.min(100, row.risk))}%`,
                          background: RISK_BAND_COLOR[band],
                        }}
                      />
                    </span>
                    <span className={`pill pill-${band}`}>{band}</span>
                  </div>
                )}
              </td>
              <td>{formatRul(row.rulCycles)}</td>
              <td>
                <VibrationCell row={row} />
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
    </div>
  );
}
