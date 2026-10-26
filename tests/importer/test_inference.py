"""Unit tests for column type inference and value conversion (no database needed)."""
from datetime import date, datetime
from decimal import Decimal

from mssql_mcp_server.importer.inference import ColumnSpec, ColumnStats, convert, to_text
from mssql_mcp_server.importer.sources import RawCell


def infer(values, fmt=None):
    s = ColumnStats()
    for v in values:
        s.observe(None if v is None else RawCell(v, fmt))
    return s.sql_type(), s.nullable


def test_bit():
    assert infer([True, False]) == ("bit", False)


def test_int_from_floats():
    assert infer([1.0, 2.0, 3]) == ("int", False)


def test_int_range_edges():
    assert infer([-(2**31), 2**31 - 1]) == ("int", False)
    assert infer([2**31]) == ("bigint", False)
    assert infer([-(2**31) - 1]) == ("bigint", False)


def test_bigint():
    assert infer([3_000_000_000]) == ("bigint", False)


def test_beyond_bigint_becomes_decimal():
    assert infer([2**63]) == ("decimal(19,0)", False)


def test_decimal():
    assert infer([1.5, 12.25]) == ("decimal(4,2)", False)


def test_decimal_small_magnitude():
    assert infer([0.3]) == ("decimal(2,1)", False)
    assert infer([0.00001]) == ("decimal(6,5)", False)


def test_decimal_mixed_with_integers():
    assert infer([1000, 0.5]) == ("decimal(5,1)", False)


def test_float_when_precision_exceeds_38():
    assert infer([1.5, 1e36]) == ("decimal(38,1)", False)
    assert infer([1.5, 1e37]) == ("float", False)


def test_float_artifact():
    assert infer([0.1 + 0.2]) == ("decimal(2,1)", False)


def test_date():
    assert infer([datetime(2024, 2, 1)]) == ("date", False)


def test_datetime2():
    assert infer([datetime(2024, 2, 1), datetime(2024, 2, 1, 13, 45)]) == ("datetime2", False)


def test_text():
    assert infer(["abcde"]) == ("nvarchar(8)", False)


def test_text_looking_number_stays_text():
    assert infer(["00123"]) == ("nvarchar(8)", False)


def test_zero_padded():
    assert infer([123, 7], "00000") == ("nvarchar(8)", False)


def test_mixed_number_text():
    assert infer([5.0, "x"]) == ("nvarchar(1)", False)


def test_bool_and_number_mix():
    assert infer([True, 1]) == ("nvarchar(4)", False)


def test_number_and_datetime_mix():
    assert infer([1, datetime(2024, 2, 1)]) == ("nvarchar(16)", False)


def test_empty_column():
    assert infer([None, None]) == ("nvarchar(255)", True)


def test_no_observations_is_nullable():
    s = ColumnStats()
    assert s.sql_type() == "nvarchar(255)"
    assert s.nullable is True


def test_nullable():
    assert infer([1, None]) == ("int", True)


def test_long_text_max():
    assert infer(["x" * 4001]) == ("nvarchar(max)", False)


def test_cap_4000():
    assert infer(["x" * 3000]) == ("nvarchar(4000)", False)


def test_to_text_forms():
    assert to_text(RawCell(5.0)) == "5"
    assert to_text(RawCell(0.1 + 0.2)) == "0.3"
    assert to_text(RawCell(123, "00000")) == "00123"
    assert to_text(RawCell(datetime(2024, 2, 1))) == "2024-02-01"
    assert to_text(RawCell(datetime(2024, 2, 1, 13, 45))) == "2024-02-01 13:45:00"
    assert to_text(RawCell(True)) == "TRUE"
    assert to_text(RawCell(False)) == "FALSE"
    assert to_text(RawCell(7)) == "7"
    assert to_text(RawCell("00123")) == "00123"
    assert to_text(RawCell(datetime(2024, 2, 1, 13, 45, 1, 500))) == "2024-02-01 13:45:01.000500"


def test_non_zero_padded_formats_keep_plain_numbers():
    assert to_text(RawCell(7, "0")) == "7"
    assert to_text(RawCell(7, "0.00")) == "7"
    assert infer([7, 8], "0") == ("int", False)


def test_convert():
    assert convert(RawCell(1.5), "decimal(4,2)") == Decimal("1.5")
    assert convert(RawCell(datetime(2024, 2, 1)), "date") == date(2024, 2, 1)
    assert convert(RawCell(3.0), "int") == 3
    assert convert(RawCell(123, "00000"), "nvarchar(8)") == "00123"
    assert convert(None, "int") is None


def test_convert_other_types():
    assert convert(RawCell(True), "bit") is True
    assert convert(RawCell(3_000_000_000), "bigint") == 3_000_000_000
    assert convert(RawCell(0.1 + 0.2), "decimal(2,1)") == Decimal("0.3")
    assert convert(RawCell(2), "decimal(4,2)") == Decimal("2")
    assert convert(RawCell(2**63), "decimal(19,0)") == Decimal(2**63)
    assert convert(RawCell(1.25), "float") == 1.25
    dt = datetime(2024, 2, 1, 13, 45)
    assert convert(RawCell(dt), "datetime2") == dt
    assert convert(RawCell("abc"), "nvarchar(max)") == "abc"
    assert convert(RawCell(5.0), "nvarchar(1)") == "5"


def test_column_spec_is_frozen_value():
    spec = ColumnSpec("a", "int", True)
    assert (spec.name, spec.sql_type, spec.nullable) == ("a", "int", True)


def test_emoji_length_utf16():
    assert infer(["😀" * 4]) == ("nvarchar(8)", False)


def test_pow2_boundaries():
    assert infer(["x" * 8]) == ("nvarchar(8)", False)
    assert infer(["x" * 9]) == ("nvarchar(16)", False)


def test_exactly_4000():
    assert infer(["x" * 4000]) == ("nvarchar(4000)", False)


def test_zero_padded_is_rounded_and_sign_aware():
    assert to_text(RawCell(12.7, "00000")) == "00013"
    assert to_text(RawCell(12.5, "00000")) == "00013"  # half away from zero, like Excel
    assert to_text(RawCell(12.4, "00000")) == "00012"
    assert to_text(RawCell(-5, "00000")) == "-00005"
    assert to_text(RawCell(-12.5, "00000")) == "-00013"
    assert to_text(RawCell(1234567, "00000")) == "1234567"
    assert convert(RawCell(-5.0, "00000"), "nvarchar(8)") == "-00005"
    assert infer([-5, 7], "00000") == ("nvarchar(8)", False)
