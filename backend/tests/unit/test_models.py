from app.models import (
    Asset,
    AssetDependency,
    AssetStatus,
    Base,
    Facility,
    FacilityMapUpload,
    RiskScore,
    Role,
    SensorReading,
    Tenant,
    User,
)


def test_metadata_has_exactly_the_expected_tables():
    assert set(Base.metadata.tables) == {
        "tenants",
        "users",
        "facilities",
        "sensor_readings",
        "facility_map_uploads",
        "assets",
        "asset_dependencies",
        "risk_scores",
    }


def test_role_enum_values():
    assert {r.value for r in Role} == {
        "superadmin",
        "tenant_admin",
        "facility_manager",
        "technician",
        "viewer",
    }


def test_tenant_columns():
    cols = Tenant.__table__.columns
    assert set(cols.keys()) == {"id", "name", "plan_tier", "created_at"}
    assert cols["id"].primary_key
    assert not cols["name"].nullable
    assert not cols["plan_tier"].nullable


def test_user_columns_and_fk():
    cols = User.__table__.columns
    assert set(cols.keys()) == {
        "id",
        "tenant_id",
        "email",
        "role",
        "hashed_password",
        "created_at",
    }
    assert not cols["tenant_id"].nullable
    fk_targets = {fk.column.table.name for fk in cols["tenant_id"].foreign_keys}
    assert fk_targets == {"tenants"}
    unique_constraints = [
        c for c in User.__table__.constraints if c.name == "uq_users_tenant_email"
    ]
    assert len(unique_constraints) == 1


def test_facility_columns_and_fk():
    cols = Facility.__table__.columns
    assert set(cols.keys()) == {
        "id",
        "tenant_id",
        "name",
        "map_file_ref",
        "bounds_geojson",
        "created_at",
    }
    assert not cols["tenant_id"].nullable
    assert cols["map_file_ref"].nullable
    assert cols["bounds_geojson"].nullable
    fk_targets = {fk.column.table.name for fk in cols["tenant_id"].foreign_keys}
    assert fk_targets == {"tenants"}


def test_sensor_reading_columns_and_fk():
    cols = SensorReading.__table__.columns
    assert set(cols.keys()) == {
        "id",
        "timestamp",
        "tenant_id",
        "asset_id",
        "sensor_type",
        "value",
        "unit",
        "created_at",
    }
    assert cols["id"].primary_key
    assert cols["timestamp"].primary_key
    assert not cols["tenant_id"].nullable
    assert not cols["asset_id"].nullable
    # No FK on tenant_id: sensor_readings lives on a separate physical TimescaleDB
    # instance from tenants (see migrations_timescale/), and Postgres has no
    # cross-database foreign keys. Tenancy is enforced by RLS alone.
    assert not cols["tenant_id"].foreign_keys
    # No FK on asset_id either, even though `assets` exists now (issue 2.6): assets
    # lives in the main Supabase database, sensor_readings in the separate physical
    # TimescaleDB instance — still no cross-database FK possible, same reasoning as
    # tenant_id above.
    assert not cols["asset_id"].foreign_keys


def test_facility_map_upload_columns_and_fks():
    cols = FacilityMapUpload.__table__.columns
    assert set(cols.keys()) == {
        "id",
        "tenant_id",
        "facility_id",
        "original_filename",
        "storage_key",
        "format",
        "status",
        "status_detail",
        "tile_prefix",
        "created_at",
    }
    assert not cols["tenant_id"].nullable
    assert not cols["facility_id"].nullable
    assert not cols["storage_key"].nullable
    assert cols["status_detail"].nullable
    assert {fk.column.table.name for fk in cols["tenant_id"].foreign_keys} == {"tenants"}
    assert {fk.column.table.name for fk in cols["facility_id"].foreign_keys} == {"facilities"}


def test_asset_status_enum_values():
    assert {s.value for s in AssetStatus} == {"operational", "maintenance", "offline"}


def test_asset_columns_and_fks():
    cols = Asset.__table__.columns
    assert set(cols.keys()) == {
        "id",
        "tenant_id",
        "facility_id",
        "name",
        "type",
        "x",
        "y",
        "status",
        "installed_date",
        "manufacturer",
        "model",
        "created_at",
    }
    assert not cols["tenant_id"].nullable
    assert not cols["facility_id"].nullable
    assert not cols["name"].nullable
    assert not cols["x"].nullable
    assert not cols["y"].nullable
    assert cols["installed_date"].nullable
    assert cols["manufacturer"].nullable
    assert cols["model"].nullable
    assert {fk.column.table.name for fk in cols["tenant_id"].foreign_keys} == {"tenants"}
    assert {fk.column.table.name for fk in cols["facility_id"].foreign_keys} == {"facilities"}


def test_asset_dependency_columns_and_fks():
    cols = AssetDependency.__table__.columns
    assert set(cols.keys()) == {
        "id",
        "tenant_id",
        "facility_id",
        "parent_asset_id",
        "child_asset_id",
        "created_at",
    }
    assert not cols["tenant_id"].nullable
    assert not cols["facility_id"].nullable
    assert not cols["parent_asset_id"].nullable
    assert not cols["child_asset_id"].nullable
    assert {fk.column.table.name for fk in cols["tenant_id"].foreign_keys} == {"tenants"}
    assert {fk.column.table.name for fk in cols["facility_id"].foreign_keys} == {"facilities"}
    assert {fk.column.table.name for fk in cols["parent_asset_id"].foreign_keys} == {"assets"}
    assert {fk.column.table.name for fk in cols["child_asset_id"].foreign_keys} == {"assets"}

    constraint_names = {c.name for c in AssetDependency.__table__.constraints}
    assert "uq_asset_dependencies_edge" in constraint_names
    assert "ck_asset_dependencies_no_self_loop" in constraint_names


def test_risk_score_columns_and_fks():
    cols = RiskScore.__table__.columns
    assert set(cols.keys()) == {
        "id",
        "tenant_id",
        "facility_id",
        "asset_id",
        "score",
        "model_version",
        "factors_json",
        "computed_at",
    }
    assert not cols["tenant_id"].nullable
    assert not cols["facility_id"].nullable
    assert not cols["asset_id"].nullable
    assert not cols["score"].nullable
    assert not cols["model_version"].nullable
    assert not cols["factors_json"].nullable
    assert {fk.column.table.name for fk in cols["tenant_id"].foreign_keys} == {"tenants"}
    assert {fk.column.table.name for fk in cols["facility_id"].foreign_keys} == {"facilities"}
    assert {fk.column.table.name for fk in cols["asset_id"].foreign_keys} == {"assets"}

    constraint_names = {c.name for c in RiskScore.__table__.constraints}
    assert "ck_risk_scores_score_range" in constraint_names
