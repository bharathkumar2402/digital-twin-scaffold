from app.models import Base, Facility, FacilityMapUpload, Role, SensorReading, Tenant, User


def test_metadata_has_exactly_the_expected_tables():
    assert set(Base.metadata.tables) == {
        "tenants",
        "users",
        "facilities",
        "sensor_readings",
        "facility_map_uploads",
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
    # No FK on asset_id yet: `assets` doesn't exist until Phase 2 task 6.
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
