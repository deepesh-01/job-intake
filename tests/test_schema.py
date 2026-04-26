from sheet import schema


def test_column_count_matches_schema_version():
    # Design doc §13.1 spec was 25 columns at v1.
    # v2 added `resume_match` (Phase 1) → 26 columns.
    assert schema.SCHEMA_VERSION == 2
    assert len(schema.JOBS_COLUMNS) == 26


def test_col_letter_works():
    assert schema.col_letter("id") == "A"
    assert schema.col_letter("notes") == "Y"
    assert schema.col_letter("status") == "R"
    assert schema.col_letter("resume_match") == "Z"


def test_status_enum_complete():
    assert schema.STATUS_NEW in schema.VALID_STATUSES
    assert schema.STATUS_TAILOR in schema.VALID_STATUSES
    assert schema.STATUS_READY in schema.VALID_STATUSES
    assert schema.STATUS_APPLIED in schema.VALID_STATUSES
    assert schema.STATUS_REJECTED in schema.VALID_STATUSES
    assert schema.STATUS_SKIP in schema.VALID_STATUSES
