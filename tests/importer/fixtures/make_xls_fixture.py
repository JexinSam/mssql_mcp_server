"""Regenerate sample.xls (run once; the binary is committed).

    uv run --with xlwt python tests/importer/fixtures/make_xls_fixture.py

xlwt is deliberately not a project dependency.
"""
from datetime import datetime
from pathlib import Path

import xlwt

OUT = Path(__file__).with_name("sample.xls")


def main() -> None:
    book = xlwt.Workbook()
    sheet = book.add_sheet("Data")
    code_style = xlwt.easyxf(num_format_str="00000")
    date_style = xlwt.easyxf(num_format_str="yyyy-mm-dd")

    for col, title in enumerate(["Code", "Amount", "When", "Flag"]):
        sheet.write(0, col, title)
    rows = [
        (123, 1.5, datetime(2024, 2, 1), True),
        (7, 2.0, datetime(2024, 2, 2), False),
    ]
    for r, (code, amount, when, flag) in enumerate(rows, start=1):
        sheet.write(r, 0, code, code_style)
        sheet.write(r, 1, amount)
        sheet.write(r, 2, when, date_style)
        sheet.write(r, 3, flag)
    book.save(str(OUT))


if __name__ == "__main__":
    main()
