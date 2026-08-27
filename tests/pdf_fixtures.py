"""Deterministic, standard-library PDF fixtures for Task 5 tests."""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Sequence

_PADDING = bytes.fromhex(
    "28bf4e5e4e758a4164004e56fffa01082e2e00b6d0683e802f0ca9fe6453697a"
)


def _rc4(key: bytes, value: bytes) -> bytes:
    state = list(range(256))
    index = 0
    for position in range(256):
        index = (index + state[position] + key[position % len(key)]) % 256
        state[position], state[index] = state[index], state[position]
    output = bytearray()
    index = 0
    position = 0
    for byte in value:
        position = (position + 1) % 256
        index = (index + state[position]) % 256
        state[position], state[index] = state[index], state[position]
        output.append(byte ^ state[(state[position] + state[index]) % 256])
    return bytes(output)


def _unicode_cmap(characters: Sequence[str]) -> bytes:
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<0000> <FFFF>",
        "endcodespacerange",
        f"{len(characters)} beginbfchar",
    ]
    lines.extend(
        f"<{index:04X}> <{ord(character):04X}>"
        for index, character in enumerate(characters, start=1)
    )
    lines.extend(
        [
            "endbfchar",
            "endcmap",
            "CMapName currentdict /CMap defineresource pop",
            "end",
            "end",
        ]
    )
    return ("\n".join(lines) + "\n").encode("ascii")


def _pdf(pages: Sequence[str], *, encrypted: bool = False) -> bytes:
    characters = tuple(dict.fromkeys("".join(pages)))
    codes = {character: index for index, character in enumerate(characters, start=1)}
    catalog_id = 1
    pages_id = 2
    page_ids = tuple(range(3, 3 + len(pages)))
    font_id = 3 + len(pages)
    descendant_id = font_id + 1
    cmap_id = font_id + 2
    content_ids = tuple(range(font_id + 3, font_id + 3 + len(pages)))
    encrypt_id = font_id + 3 + len(pages) if encrypted else None
    file_id = bytes.fromhex("00112233445566778899aabbccddeeff")

    objects: dict[int, bytes] = {
        catalog_id: f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode(),
        pages_id: (
            f"<< /Type /Pages /Kids [{' '.join(f'{i} 0 R' for i in page_ids)}] "
            f"/Count {len(page_ids)} >>"
        ).encode(),
        font_id: (
            f"<< /Type /Font /Subtype /Type0 /BaseFont /AAOSFixture "
            f"/Encoding /Identity-H /DescendantFonts [{descendant_id} 0 R] "
            f"/ToUnicode {cmap_id} 0 R >>"
        ).encode(),
        descendant_id: (
            "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /AAOSFixture "
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
            "/DW 1000 >>"
        ).encode(),
        cmap_id: _stream(_unicode_cmap(characters)),
    }
    for page_id, content_id, text in zip(page_ids, content_ids, pages, strict=True):
        encoded = b"".join(struct.pack(">H", codes[character]) for character in text)
        stream = b"BT /F1 12 Tf 72 720 Td <" + encoded.hex().encode("ascii") + b"> Tj ET\n"
        objects[page_id] = (
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode()
        objects[content_id] = _stream(stream)
    if encrypt_id is not None:
        user_password = b"secret" + _PADDING[: len(_PADDING) - len(b"secret")]
        owner_key = hashlib.md5(_PADDING, usedforsecurity=False).digest()[:5]
        owner_entry = _rc4(owner_key, user_password)
        file_key = hashlib.md5(
            user_password + owner_entry + struct.pack("<i", -4) + file_id,
            usedforsecurity=False,
        ).digest()[:5]
        user_entry = _rc4(file_key, _PADDING)
        objects[encrypt_id] = (
            f"<< /Filter /Standard /V 1 /Length 40 /O <{owner_entry.hex()}> "
            f"/U <{user_entry.hex()}> /P -4 /R 2 >>"
        ).encode()
        for object_id in (*content_ids, cmap_id):
            object_key = hashlib.md5(
                file_key + struct.pack("<I", object_id)[:3] + b"\x00\x00",
                usedforsecurity=False,
            ).digest()[:10]
            plain = objects[object_id]
            prefix, payload, suffix = plain.partition(b"\nstream\n")
            if not payload:
                raise AssertionError("fixture stream missing")
            payload, suffix = suffix.split(b"\nendstream", maxsplit=1)
            objects[object_id] = prefix + b"\nstream\n" + _rc4(object_key, payload) + b"\nendstream" + suffix
    trailer_extra = f" /Encrypt {encrypt_id} 0 R" if encrypt_id is not None else ""
    return _serialize(objects, trailer_extra=trailer_extra)


def _stream(payload: bytes) -> bytes:
    return f"<< /Length {len(payload)} >>\nstream\n".encode() + payload + b"\nendstream"


def _serialize(objects: dict[int, bytes], *, trailer_extra: str = "") -> bytes:
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_id in range(1, max(objects) + 1):
        offsets.append(len(output))
        output.extend(f"{object_id} 0 obj\n".encode())
        output.extend(objects[object_id])
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R /ID [<00112233445566778899AABBCCDDEEFF> "
        f"<00112233445566778899AABBCCDDEEFF>]{trailer_extra} >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode()
    )
    return bytes(output)


def valid_one_page_pdf() -> bytes:
    return _pdf(("Hello AAOS",))


def mixed_unicode_pdf() -> bytes:
    return _pdf(("مرحبا AAOS",))


def valid_multipage_pdf() -> bytes:
    return _pdf(("Page one", "Page two", "Page three"))


def mixed_text_empty_pdf() -> bytes:
    return _pdf(("Visible text", "   "))


def all_empty_pdf() -> bytes:
    return _pdf(("", "   "))


def zero_page_pdf() -> bytes:
    return _pdf(())


def malformed_pdf() -> bytes:
    return b"%PDF-1.7\nnot a valid document"


def html_as_pdf() -> bytes:
    return b"<html><body>not a PDF</body></html>"


def password_required_pdf() -> bytes:
    return _pdf(("secret",), encrypted=True)
