from app.models import Base, Facility, Role, Tenant, User


def test_metadata_has_exactly_the_three_core_tables():
    assert set(Base.metadata.tables) == {"tenants", "users", "facilities"}


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
