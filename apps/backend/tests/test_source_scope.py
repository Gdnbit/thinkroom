from app.utils.source_scope import clean_source_ids


def test_clean_source_ids_normalizes_and_deduplicates_values():
    assert clean_source_ids([" src-1 ", "", "src-1", "src-2", None, 42]) == ["src-1", "src-2"]


def test_clean_source_ids_accepts_single_string_and_ignores_invalid_values():
    assert clean_source_ids(" src-1 ") == ["src-1"]
    assert clean_source_ids(None) == []
    assert clean_source_ids(123) == []
