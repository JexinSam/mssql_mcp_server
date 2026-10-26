"""Infer SQL Server column types from native Excel cells and convert values for insert.

Pure logic, no I/O. Only the native cell type counts: text cells stay text even
when they look like numbers or dates.
"""
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from .sources import RawCell

_ZERO_PADDED = re.compile(r"0{2,}")
_DECIMAL = re.compile(r"decimal\((\d+),(\d+)\)")
_INT_MIN, _INT_MAX = -(2**31), 2**31 - 1
_BIGINT_MIN, _BIGINT_MAX = -(2**63), 2**63 - 1
_MAX_DECIMAL_PRECISION = 38
_MAX_NVARCHAR = 4000
_EMPTY_COLUMN_TYPE = "nvarchar(255)"

_BOOL, _NUMBER, _DATETIME, _TEXT = "bool", "number", "datetime", "text"


@dataclass(frozen=True)
class ColumnSpec:
    """One destination column. ``sql_type`` is lowercase, e.g. ``decimal(5,2)``."""

    name: str
    sql_type: str
    nullable: bool


def _normalize_float(f: float) -> float:
    """Drop binary noise: 0.1 + 0.2 becomes 0.3."""
    return float(format(f, ".15g"))


def _is_zero_padded(cell: RawCell) -> bool:
    value = cell.value
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and cell.number_format is not None
        and _ZERO_PADDED.fullmatch(cell.number_format) is not None
    )


def _number(cell: RawCell) -> int | float:
    """The cell's numeric value; floats normalised, integral floats become ints."""
    value = cell.value
    assert isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(value, float):
        value = _normalize_float(value)
        if value.is_integer():
            # Via repr so 1e37 gives 10**37, not the float's binary expansion.
            return int(Decimal(repr(value)))
    return value


def _kind(cell: RawCell) -> str:
    value = cell.value
    if isinstance(value, bool):
        return _BOOL
    if isinstance(value, (int, float)):
        return _TEXT if _is_zero_padded(cell) else _NUMBER
    if isinstance(value, datetime):
        return _DATETIME
    return _TEXT


def _utf16_length(text: str) -> int:
    """Length in UTF-16 code units, which is how SQL Server sizes nvarchar(n)."""
    return len(text.encode("utf-16-le")) // 2


def _zero_padded_text(number: int | float, digits: int) -> str:
    """Render like Excel's "000…" format: rounded half away from zero, sign kept in front."""
    exact = Decimal(number) if isinstance(number, int) else Decimal(repr(number))
    rounded = int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    sign = "-" if rounded < 0 else ""
    return sign + str(abs(rounded)).zfill(digits)


def to_text(cell: RawCell) -> str:
    """Canonical text form of a non-null cell."""
    value = cell.value
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        number = _number(cell)
        if _is_zero_padded(cell):
            assert cell.number_format is not None
            return _zero_padded_text(number, len(cell.number_format))
        return str(number) if isinstance(number, int) else repr(number)
    if isinstance(value, datetime):
        if value.microsecond:
            return value.strftime("%Y-%m-%d %H:%M:%S.%f")
        if value.hour or value.minute or value.second:
            return value.strftime("%Y-%m-%d %H:%M:%S")
        return value.strftime("%Y-%m-%d")
    assert isinstance(value, str)
    return value


def _decimal_shape(number: int | float) -> tuple[int, int]:
    """(integer digits, scale) of a number, e.g. 12.25 -> (2, 2)."""
    int_digits = len(str(abs(int(number))))
    if isinstance(number, int):
        return int_digits, 0
    exponent = Decimal(repr(number)).as_tuple().exponent
    assert isinstance(exponent, int)
    return int_digits, max(0, -exponent)


class ColumnStats:
    """Accumulates observations for one column and derives its SQL type."""

    def __init__(self) -> None:
        self._saw_null = False
        self._kinds: set[str] = set()
        self._max_text_len = 0
        # Numbers
        self._min_int: int | None = None
        self._max_int: int | None = None
        self._max_int_digits = 0
        self._max_scale = 0
        self._has_fraction = False
        # Datetimes
        self._has_time = False

    def observe(self, cell: RawCell | None) -> None:
        if cell is None or cell.value is None:
            self._saw_null = True
            return
        kind = _kind(cell)
        self._kinds.add(kind)
        self._max_text_len = max(self._max_text_len, _utf16_length(to_text(cell)))
        if kind == _NUMBER:
            self._observe_number(_number(cell))
        elif kind == _DATETIME:
            value = cell.value
            assert isinstance(value, datetime)
            if value.time() != datetime.min.time():
                self._has_time = True

    def _observe_number(self, number: int | float) -> None:
        int_digits, scale = _decimal_shape(number)
        self._max_int_digits = max(self._max_int_digits, int_digits)
        self._max_scale = max(self._max_scale, scale)
        if isinstance(number, int):
            self._min_int = number if self._min_int is None else min(self._min_int, number)
            self._max_int = number if self._max_int is None else max(self._max_int, number)
        else:
            self._has_fraction = True

    @property
    def nullable(self) -> bool:
        """True when a null was seen, or nothing non-null was (e.g. a header-only sheet)."""
        return self._saw_null or not self._kinds

    def sql_type(self) -> str:
        if not self._kinds:
            return _EMPTY_COLUMN_TYPE
        if len(self._kinds) > 1:
            return self._nvarchar()
        (kind,) = self._kinds
        if kind == _BOOL:
            return "bit"
        if kind == _NUMBER:
            return self._numeric()
        if kind == _DATETIME:
            return "datetime2" if self._has_time else "date"
        return self._nvarchar()

    def _numeric(self) -> str:
        if not self._has_fraction:
            assert self._min_int is not None and self._max_int is not None
            if _INT_MIN <= self._min_int and self._max_int <= _INT_MAX:
                return "int"
            if _BIGINT_MIN <= self._min_int and self._max_int <= _BIGINT_MAX:
                return "bigint"
        precision = self._max_int_digits + self._max_scale
        if precision > _MAX_DECIMAL_PRECISION:
            return "float"
        return f"decimal({precision},{self._max_scale})"

    def _nvarchar(self) -> str:
        length = self._max_text_len
        if length > _MAX_NVARCHAR:
            return "nvarchar(max)"
        size = 1
        while size < length:
            size *= 2
        return f"nvarchar({min(size, _MAX_NVARCHAR)})"


def convert(cell: RawCell | None, sql_type: str) -> object:
    """Convert a cell to the Python value to insert into a column of ``sql_type``."""
    if cell is None or cell.value is None:
        return None
    if sql_type == "bit":
        return bool(cell.value)
    if sql_type in ("int", "bigint"):
        return int(_number(cell))
    if sql_type == "float":
        return float(_number(cell))
    if _DECIMAL.fullmatch(sql_type):
        number = _number(cell)
        return Decimal(number) if isinstance(number, int) else Decimal(repr(number))
    value = cell.value
    if sql_type == "date":
        assert isinstance(value, datetime)
        return value.date()
    if sql_type == "datetime2":
        assert isinstance(value, datetime)
        return value
    return to_text(cell)
