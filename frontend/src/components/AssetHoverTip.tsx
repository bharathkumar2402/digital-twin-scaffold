import type { MapLayerMouseEvent } from "maplibre-gl";
import { useEffect, useRef, useState } from "react";

import { RISK_BAND_COLOR, riskBand } from "../lib/assetHealth";
import type { Asset } from "../types/api";

const MARKER_LAYER_ID = "facility-assets-circle";

interface Tip {
  x: number;
  y: number;
  asset: Asset;
}

interface AssetHoverTipProps {
  map: maplibregl.Map | null;
  assets: Asset[];
  riskScores: Map<string, number>;
}

// A small card beside the marker under the pointer: name, type, risk. It is an HTML overlay
// driven by the marker layer's own mouse events, so it does not touch the layer itself.
export function AssetHoverTip({ map, assets, riskScores }: AssetHoverTipProps): React.JSX.Element | null {
  const [tip, setTip] = useState<Tip | null>(null);
  const assetsRef = useRef(assets);
  assetsRef.current = assets;

  useEffect(() => {
    if (!map) return;
    const onMove = (event: MapLayerMouseEvent) => {
      const id = event.features?.[0]?.properties?.id as string | undefined;
      const asset = assetsRef.current.find((a) => a.id === id);
      setTip(asset ? { x: event.point.x, y: event.point.y, asset } : null);
    };
    const onLeave = () => setTip(null);
    map.on("mousemove", MARKER_LAYER_ID, onMove);
    map.on("mouseleave", MARKER_LAYER_ID, onLeave);
    return () => {
      map.off("mousemove", MARKER_LAYER_ID, onMove);
      map.off("mouseleave", MARKER_LAYER_ID, onLeave);
    };
  }, [map]);

  if (!tip) return null;
  const risk = riskScores.get(tip.asset.id);
  return (
    <div className="card hover-tip" role="tooltip" style={{ left: tip.x + 16, top: tip.y + 16 }}>
      <strong>{tip.asset.name}</strong>
      <span>{tip.asset.type.replace(/_/g, " ")}</span>
      <span style={{ color: risk === undefined ? undefined : RISK_BAND_COLOR[riskBand(risk)] }}>
        {risk === undefined ? "Not scored yet" : `Risk ${Math.round(risk)} (${riskBand(risk)})`}
      </span>
    </div>
  );
}
