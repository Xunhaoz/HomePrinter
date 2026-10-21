import io
import zipfile
from xml.etree import ElementTree

import pymupdf

from homeprinter.config import Settings
from homeprinter.scanner import NS, ScannerClient, SourceCaps, _combine


def one_page_pdf(text: str) -> bytes:
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), text)
        return doc.tobytes()


def test_feeder_pdfs_are_merged_in_order():
    result = _combine([one_page_pdf("one"), one_page_pdf("two"), one_page_pdf("three")], "pdf")
    assert (result.media_type, result.extension, result.pages) == ("application/pdf", "pdf", 3)
    with pymupdf.open(stream=result.content, filetype="pdf") as doc:
        assert [page.get_text().strip() for page in doc] == ["one", "two", "three"]


def test_feeder_jpegs_are_zipped():
    result = _combine([b"jpeg-1", b"jpeg-2"], "jpeg")
    assert (result.media_type, result.extension, result.pages) == ("application/zip", "zip", 2)
    with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
        assert archive.namelist() == ["page-001.jpg", "page-002.jpg"]
        assert archive.read("page-002.jpg") == b"jpeg-2"


def test_single_document_is_returned_as_is():
    pdf = one_page_pdf("only")
    assert _combine([pdf], "pdf").content == pdf
    assert _combine([b"jpeg"], "jpeg").extension == "jpg"


def test_settings_xml_is_valid_and_clamped_to_the_source():
    caps = SourceCaps(max_width=2550, max_height=3510, resolutions=[300], color_modes=[], formats=[])
    xml = ScannerClient(Settings())._settings_xml(caps, "flatbed", "grayscale", 300, "pdf", "legal")
    root = ElementTree.fromstring(xml)
    assert root.findtext("pwg:InputSource", namespaces=NS) == "Platen"
    assert root.findtext("scan:ColorMode", namespaces=NS) == "Grayscale8"
    assert root.findtext("pwg:DocumentFormat", namespaces=NS) == "application/pdf"
    region = root.find("pwg:ScanRegions/pwg:ScanRegion", NS)
    # Legal is 4200 high, more than the flatbed's 3510.
    assert (region.findtext("pwg:Width", namespaces=NS), region.findtext("pwg:Height", namespaces=NS)) == ("2550", "3510")
