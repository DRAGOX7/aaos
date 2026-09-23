"""Small deterministic WordprocessingML fixtures for DOCX parser tests."""

from __future__ import annotations

from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels"
    ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>
"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
    Target="word/document.xml"/>
</Relationships>
"""

_DOCUMENT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
    Target="styles.xml"/>
</Relationships>
"""


def _styles_xml() -> str:
    heading_styles = "".join(
        (
            f'<w:style w:type="paragraph" w:styleId="Heading{level}">'
            f'<w:name w:val="heading {level}"/>'
            '<w:basedOn w:val="Normal"/>'
            "</w:style>"
        )
        for level in range(1, 10)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main">'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:name w:val="Normal"/>'
        "</w:style>"
        f"{heading_styles}"
        "</w:styles>"
    )


def _text(value: str) -> str:
    return (
        '<w:r><w:t xml:space="preserve">'
        f"{escape(value)}"
        "</w:t></w:r>"
    )


def _paragraph(value: str, *, style: str | None = None) -> str:
    properties = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{properties}{_text(value)}</w:p>"


def _cell(*paragraphs: str) -> str:
    content = "".join(_paragraph(value) for value in paragraphs)
    return f"<w:tc><w:tcPr/>{content}</w:tc>"


def _table(rows: tuple[tuple[tuple[str, ...], ...], ...]) -> str:
    column_count = max(len(row) for row in rows)
    grid = "".join("<w:gridCol/>" for _ in range(column_count))
    row_xml = "".join(
        "<w:tr>" + "".join(_cell(*cell) for cell in row) + "</w:tr>"
        for row in rows
    )
    return f"<w:tbl><w:tblPr/><w:tblGrid>{grid}</w:tblGrid>{row_xml}</w:tbl>"


def _package(body_xml: str) -> bytes:
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main">'
        f"<w:body>{body_xml}<w:sectPr/></w:body>"
        "</w:document>"
    )

    output = BytesIO()
    with ZipFile(output, "w") as archive:
        _write(archive, "[Content_Types].xml", _CONTENT_TYPES)
        _write(archive, "_rels/.rels", _ROOT_RELS)
        _write(archive, "word/document.xml", document_xml)
        _write(archive, "word/_rels/document.xml.rels", _DOCUMENT_RELS)
        _write(archive, "word/styles.xml", _styles_xml())
    return output.getvalue()


def _write(archive: ZipFile, name: str, value: str) -> None:
    info = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    archive.writestr(info, value.encode("utf-8"))


def mixed_structure_docx() -> bytes:
    body = "".join(
        (
            _paragraph("Course Overview", style="Heading1"),
            _paragraph("تعلم الآلة — Machine Learning"),
            _table(
                (
                    (("A1",), ("B1",)),
                    (("A2", "continued"), ("",)),
                )
            ),
            _paragraph("Between tables"),
            _table(((("C1",), ("D1",)),)),
            _paragraph("Details", style="Heading3"),
        )
    )
    return _package(body)


def all_heading_levels_docx() -> bytes:
    return _package(
        "".join(
            _paragraph(f"Heading {level}", style=f"Heading{level}")
            for level in range(1, 10)
        )
    )


def blank_paragraph_docx() -> bytes:
    return _package(_paragraph(""))


def merged_cell_docx() -> bytes:
    table = (
        "<w:tbl><w:tblPr/>"
        "<w:tblGrid><w:gridCol/><w:gridCol/></w:tblGrid>"
        "<w:tr>"
        "<w:tc>"
        '<w:tcPr><w:gridSpan w:val="2"/></w:tcPr>'
        + _paragraph("merged")
        + "</w:tc>"
        "</w:tr>"
        "</w:tbl>"
    )
    return _package(table)


def vertical_merged_cell_docx() -> bytes:
    table = (
        "<w:tbl><w:tblPr/>"
        "<w:tblGrid><w:gridCol/></w:tblGrid>"
        "<w:tr>"
        "<w:tc>"
        '<w:tcPr><w:vMerge w:val="restart"/></w:tcPr>'
        + _paragraph("merged")
        + "</w:tc>"
        "</w:tr>"
        "<w:tr>"
        "<w:tc>"
        "<w:tcPr><w:vMerge/></w:tcPr>"
        + _paragraph("")
        + "</w:tc>"
        "</w:tr>"
        "</w:tbl>"
    )
    return _package(table)


def omitted_leading_cell_docx() -> bytes:
    table = (
        "<w:tbl><w:tblPr/>"
        "<w:tblGrid>"
        "<w:gridCol/><w:gridCol/><w:gridCol/>"
        "</w:tblGrid>"
        "<w:tr>"
        '<w:trPr><w:gridBefore w:val="1"/></w:trPr>'
        + _cell("second")
        + _cell("third")
        + "</w:tr>"
        "</w:tbl>"
    )
    return _package(table)


def nested_table_docx() -> bytes:
    nested = _table(((("nested",),),))
    outer_cell = (
        "<w:tc><w:tcPr/>"
        + _paragraph("before")
        + nested
        + _paragraph("after")
        + "</w:tc>"
    )
    outer = (
        "<w:tbl><w:tblPr/><w:tblGrid><w:gridCol/></w:tblGrid>"
        f"<w:tr>{outer_cell}</w:tr>"
        "</w:tbl>"
    )
    return _package(outer)
