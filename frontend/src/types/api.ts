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
