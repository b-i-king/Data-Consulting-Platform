"""
Round-trip test for every supported file format.

For each format we WRITE a tiny table with pandas (or by hand), then READ it
back through api.services.file_reader and check we get the same data. If a
reader library is missing or a format breaks, this test names the culprit.
"""

import bz2
import gzip
import io
import zipfile

import pandas as pd
import pyreadstat
import pytest

from api.services.file_reader import (
    EXCLUDED_FORMATS,
    FORMATS,
    FileReadError,
    UnsupportedFileError,
    read_file,
)

DF = pd.DataFrame({"customer": ["Ana", "Ben", "Cal"], "amount": [10.5, 20.0, 7.25]})


def _bytes(write) -> bytes:
    buf = io.BytesIO()
    write(buf)
    return buf.getvalue()


def _spss_bytes(tmp_path) -> bytes:
    path = tmp_path / "x.sav"
    pyreadstat.write_sav(DF, str(path))
    return path.read_bytes()


def _sas_xpt_bytes(tmp_path) -> bytes:
    path = tmp_path / "x.xpt"
    pyreadstat.write_xport(DF, str(path), file_format_version=5)  # pandas reads XPORT v5
    return path.read_bytes()


def samples(tmp_path) -> dict[str, bytes]:
    csv = DF.to_csv(index=False).encode()
    return {
        "a.csv": csv,
        "a.xlsx": _bytes(lambda b: DF.to_excel(b, index=False)),
        "a.tsv": DF.to_csv(index=False, sep="\t").encode(),
        "a.txt": DF.to_csv(index=False, sep="|").encode(),          # pipe, auto-detected
        "a.fwf": b"customer  amount\nAna       10.5\nBen       20.0\nCal       7.25\n",
        "a.json": DF.to_json(orient="records").encode(),
        "a.jsonl": DF.to_json(orient="records", lines=True).encode(),
        "a.html": DF.to_html(index=False).encode(),
        "a.xml": DF.to_xml(index=False, parser="etree").encode(),
        "a.parquet": _bytes(lambda b: DF.to_parquet(b)),
        "a.feather": _bytes(lambda b: DF.to_feather(b)),
        "a.orc": _bytes(lambda b: DF.to_orc(b)),
        "a.dta": _bytes(lambda b: DF.to_stata(b, write_index=False)),
        "a.sav": _spss_bytes(tmp_path),
        "a.xpt": _sas_xpt_bytes(tmp_path),
        "a.csv.gz": gzip.compress(csv),
        "a.csv.bz2": bz2.compress(csv),
    }


def test_every_sample_reads_back_the_same_data(tmp_path):
    for name, raw in samples(tmp_path).items():
        [ds] = read_file(name, raw)
        got = ds.df
        assert list(got.columns) == ["customer", "amount"], name
        assert got["customer"].tolist() == ["Ana", "Ben", "Cal"], name
        assert got["amount"].astype(float).round(2).tolist() == [10.5, 20.0, 7.25], name


def test_every_registered_format_has_a_sample(tmp_path):
    """Adding a format to FORMATS without a sample above fails this test."""
    covered = {name[name.index("."):] for name in samples(tmp_path)}
    # Formats whose sample needs a binary writer pandas doesn't have:
    untested_on_purpose = {".xlsm", ".xls", ".xlsb", ".ods", ".sas7bdat", ".zsav",
                           ".tab", ".dat", ".psv", ".prn", ".ndjson", ".htm", ".pq", ".arrow"}
    for fmt in FORMATS:
        for ext in fmt.extensions:
            assert ext in covered or ext in untested_on_purpose, f"No sample for {ext}"


def test_csv_and_excel_are_the_recommended_formats():
    assert {f.id for f in FORMATS if f.recommended} == {"csv", "excel"}
    assert [f.id for f in FORMATS][:2] == ["csv", "excel"]


def test_zip_with_several_files_gives_several_datasets():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("sales_2023.csv", DF.to_csv(index=False))
        zf.writestr("sales_2024.csv", DF.to_csv(index=False))
        zf.writestr("__MACOSX/._junk", "x")
        zf.writestr("notes.docx", "ignored")
    datasets = read_file("exports.zip", buf.getvalue())
    assert [d.name for d in datasets] == ["exports.zip › sales_2023.csv", "exports.zip › sales_2024.csv"]
    assert all(d.source_file == "exports.zip" for d in datasets)


def test_html_with_two_tables():
    page = (DF.to_html(index=False) + DF.to_html(index=False)).encode()
    assert len(read_file("report.html", page)) == 2


@pytest.mark.parametrize("name", sorted(EXCLUDED_FORMATS))
def test_unsafe_formats_are_refused(name):
    with pytest.raises(UnsupportedFileError, match="code"):
        read_file(f"data{name}", b"anything")


def test_unsafe_format_inside_compression_is_refused():
    with pytest.raises(UnsupportedFileError):
        read_file("data.pkl.gz", gzip.compress(b"anything"))


def test_zip_bomb_is_refused(monkeypatch):
    from api.core import config
    monkeypatch.setattr(config.get_settings(), "max_decompressed_mb", 1)
    big = gzip.compress(b"a,b\n" + b"1,2\n" * 400_000)   # ~1.6 MB once expanded
    with pytest.raises(FileReadError, match="expands"):
        read_file("big.csv.gz", big)


@pytest.mark.parametrize("text, expected", [
    ("customer|amount\nAna|10\n", "|"),
    ("a;b;c\n1;2;3\n", ";"),
    ("a\tb\n1\t2\n", "\t"),
    ("name,city\n\"Lee, Ana\",Atlanta\n", ","),   # comma wins even with a quoted comma... falls back
    ("justonecolumn\nvalue\n", ","),
])
def test_detect_delimiter(text, expected):
    from api.services.file_reader import detect_delimiter
    assert detect_delimiter(text) == expected


def test_semicolon_csv_with_decimal_commas_reads_as_numbers():
    # European/Latin American Excel saves ".csv" with ";" and 1.234,56 numbers.
    text = "name;amount\n" + "".join(f"n{i};{i}.000,5\n" for i in range(5))
    df = read_file("export.csv", text.encode())[0].df
    assert list(df.columns) == ["name", "amount"]
    assert df["amount"].tolist() == [0.5, 1000.5, 2000.5, 3000.5, 4000.5]
    # US style stays as it was: comma separator, quoted thousands untouched.
    us = read_file("us.csv", b'name,amount\n"Lee, Ana",1.25\nBo,2.5\n')[0].df
    assert us.shape == (2, 2) and us["amount"].tolist() == [1.25, 2.5]


def test_nested_json_becomes_columns_and_text():
    raw = b'{"records": [{"a": 1, "meta": {"k": 2}, "tags": ["x", "y"]}, {"a": 2, "meta": {"k": 3}, "tags": []}]}'
    df = read_file("nested.json", raw)[0].df
    assert sorted(df.columns) == ["a", "meta.k", "tags"]   # nested fields are added as "parent.child"
    assert df["tags"].tolist() == ['["x", "y"]', "[]"]          # lists stored as text, so duplicates can be checked
    lines = read_file("rows.jsonl", b'{"a": 1, "m": {"k": 1}}\n{"a": 2, "m": {"k": 2}}\n')[0].df
    assert list(lines.columns) == ["a", "m.k"]


@pytest.mark.parametrize("name, raw, says", [
    ("empty.csv", b"", "is empty"),
    ("fake.xlsx", b"not really excel", "doesn't look like a real Excel file"),
    ("ragged.csv", b"a\n1\n2,3\n", "Row 3"),
])
def test_read_errors_are_plain_english(name, raw, says):
    with pytest.raises(FileReadError, match=says):
        read_file(name, raw)


def test_blank_and_repeated_headers_in_excel_are_marked():
    # An Excel sheet whose header row has two blank cells and a repeated name.
    buf = io.BytesIO()
    pd.DataFrame([[1, "x", 3, 4, 9]], columns=["Order ID", None, "Amount", "Amount", None]).to_excel(buf, index=False)
    [ds] = read_file("orders.xlsx", buf.getvalue())
    assert list(ds.df.columns) == ["Order ID", "unnamed_column_2", "Amount", "Amount_2", "unnamed_column_5"]
