// Mirrors backend/app/schemas/requests/auth.py and
// backend/app/schemas/requests/facility_maps.py. Keep these in sync by hand — there's
// no shared schema generation between the two apps yet.

export type Role = "superadmin" | "tenant_admin" | "facility_manager" | "technician" | "viewer";

export interface AccessTokenResponse {
  access_token: string;
  token_type: string;
}

// Mirrors backend/app/models/facility_map_upload.py's UploadStatus enum.
export type FacilityMapUploadStatus =
  | "pending"
  | "processing"
  | "sanitized"
  | "tiled"
  | "failed"
  | "conversion_failed";

export interface FacilityMapUploadStatusResponse {
  id: string;
  facility_id: string;
  original_filename: string;
  format: string;
  status: FacilityMapUploadStatus;
  tile_prefix: string | null;
  tile_url_template: string | null;
}

// Mirrors backend/app/models/asset.py's AssetStatus enum.
export type AssetStatus = "operational" | "maintenance" | "offline";

// Mirrors backend/app/schemas/requests/assets.py's AssetResponse. `x`/`y` are in the
// same local-pixel coordinate space as the facility's tile pyramid (see
// FacilityMap.tsx and backend/app/models/asset.py), not lat/lon.
export interface Asset {
  id: string;
  facility_id: string;
  name: string;
  type: string;
  x: number;
  y: number;
  status: AssetStatus;
  installed_date: string | null;
  manufacturer: string | null;
  model: string | null;
}

export interface AssetCreateRequest {
  name: string;
  type: string;
  x: number;
  y: number;
  status?: AssetStatus;
  installed_date?: string | null;
  manufacturer?: string | null;
  model?: string | null;
}

export type AssetUpdateRequest = Partial<AssetCreateRequest>;

// Mirrors backend/app/schemas/requests/asset_dependencies.py's AssetDependencyResponse.
// `parent_asset_id` depends on `child_asset_id` (child is upstream) - see
// backend/app/models/asset_dependency.py's docstring for why this direction was chosen.
export interface AssetDependency {
  id: string;
  facility_id: string;
  parent_asset_id: string;
  child_asset_id: string;
}

export interface AssetDependencyCreateRequest {
  parent_asset_id: string;
  child_asset_id: string;
}

// Mirrors backend/app/schemas/requests/telemetry.py's TelemetryReadingResponse.
export interface TelemetryReading {
  sensor_type: string;
  value: number;
  unit: string;
  timestamp: string;
}

// Mirrors backend/app/schemas/ml/alert_notification.py's AlertNotification - one
// message as forwarded verbatim by /ws/alerts (issue 3.6).
export interface AlertNotification {
  asset_id: string;
  sensor_type: string;
  value: number;
  z_score: number | null;
  triggered_at: string;
}
