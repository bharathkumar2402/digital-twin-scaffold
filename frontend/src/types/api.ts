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
  floor_level?: number;
  floor_name?: string | null;
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
  // Which floor the asset stands on (0 = ground); x/y are pixels in that floor's map.
  floor_level: number;
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
  floor_level?: number;
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

// Mirrors backend/app/schemas/requests/risk_scores.py's RiskScoreResponse. `score` is
// 0-100 (see backend/app/services/risk_inference_service.py's score_facility, which
// clamps model.predict_proba's [0,1] output into that range).
export interface RiskScore {
  id: string;
  facility_id: string;
  asset_id: string;
  score: number;
  // Remaining useful life in cycles; null when the asset has no usable RUL telemetry or no
  // RUL model is published (not 0 - 0 would mean "fails now").
  rul_cycles: number | null;
  model_version: string;
  factors_json: Record<string, number | string | null>;
  computed_at: string;
}

// ---- Agent runs (mirrors backend/app/schemas/requests/agent_runs.py and the agent output
// schemas in backend/app/schemas/agent_outputs/) -------------------------------------------

export type AgentRunTrigger = "scheduled" | "user_query" | "alert";

export interface AgentRunSummary {
  id: string;
  tenant_id: string;
  facility_id: string | null;
  trigger: string;
  // "completed" | "halted_for_escalation" | "failed"
  status: string;
  duration_ms: number;
  validation_errors_json: string[];
  decision_report: string | null;
  confidence: number | null;
  created_at: string;
}

export type RiskTier = "low" | "medium" | "high" | "critical";

export interface ScoredAssetOutput {
  asset_id: string;
  risk_score: number;
  rul_cycles: number | null;
  rul_model_version: string | null;
  urgency_rank: number;
  risk_tier: RiskTier;
  predicted_failure_mode: string | null;
  primary_risk_factors: string[];
  recommended_action: string;
}

export interface RequiredPart {
  part_id: string;
  part_name: string;
  quantity: number;
}

export interface MaintenanceItem {
  item_id: string;
  asset_id: string;
  scheduled_date: string;
  priority: RiskTier;
  estimated_duration_hours: number;
  required_technician_skills: string[];
  required_parts: RequiredPart[];
}

export interface InventoryGap {
  part_id: string;
  part_name: string;
  needed_quantity: number;
  available_quantity: number;
  shortage_count: number;
  lead_time_days: number;
  impacted_asset_ids: string[];
}

export interface RouteStop {
  stop_number: number;
  asset_id: string;
  task_id: string;
  estimated_arrival_minutes: number;
  service_duration_minutes: number;
  travel_time_from_previous_minutes: number;
}

export interface DispatchRoute {
  technician_id: string;
  technician_name: string;
  assigned_stops: RouteStop[];
  total_travel_minutes: number;
  total_service_minutes: number;
  total_route_duration_minutes: number;
}

export interface CascadeResult {
  root_cause_asset_id: string;
  directly_affected_asset_ids: string[];
  downstream_shutoff_asset_ids: string[];
  total_affected_assets: number;
  critical_subsystems_interrupted: string[];
  estimated_downtime_hours: number;
  cascade_depth: number;
}

export interface AgentRunState {
  risk_scores: ScoredAssetOutput[] | null;
  maintenance_schedule: MaintenanceItem[] | null;
  inventory_gaps: InventoryGap[] | null;
  dispatch_routes: DispatchRoute[] | null;
  simulation_result: CascadeResult | null;
  halted_for_escalation?: boolean;
  escalation_reason?: string | null;
  errors?: string[];
  user_query?: string | null;
}

export interface AgentRunDetail extends AgentRunSummary {
  state_snapshot_json: AgentRunState;
}

export interface AgentRunRequest {
  trigger: AgentRunTrigger;
  user_query: string | null;
}

// Mirrors backend/app/schemas/requests/facilities.py's FacilityResponse.
export interface FacilitySummary {
  id: string;
  name: string;
  // Newest floor-plan upload that finished tiling; null if the facility has no map yet.
  latest_map_upload_id: string | null;
  asset_count: number;
  // One entry per floor that has a finished map, lowest level first.
  floors: FloorSummary[];
}

// Mirrors backend/app/schemas/requests/facilities.py's FloorResponse.
export interface FloorSummary {
  level: number;
  name: string;
  map_upload_id: string;
  asset_count: number;
}
