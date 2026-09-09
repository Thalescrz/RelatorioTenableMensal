from __future__ import annotations

import argparse
import io
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
XML = "http://www.w3.org/XML/1998/namespace"
NS = {"w": W, "wp": WP}
MONTH_PATTERN = re.compile(
    r"(?:JANEIRO|FEVEREIRO|MARÇO|ABRIL|MAIO|JUNHO|JULHO|AGOSTO|"
    r"SETEMBRO|OUTUBRO|NOVEMBRO|DEZEMBRO)\s*/\s*\d{4}",
    re.IGNORECASE,
)


def qn(namespace: str, local: str) -> str:
    return f"{{{namespace}}}{local}"


def _paragraph_text(paragraph: etree._Element) -> str:
    return "".join(paragraph.xpath(".//w:t/text()", namespaces=NS))


def _patch_document(payload: bytes, source_client: str) -> bytes:
    root = etree.fromstring(payload)
    body = root.find("w:body", NS)
    if body is None:
        raise ValueError("Documento-base sem corpo Word válido.")
    section_paragraphs = [
        paragraph
        for paragraph in body.findall("w:p", NS)
        if paragraph.find("w:pPr/w:sectPr", NS) is not None
    ]
    if len(section_paragraphs) < 2 or body.find("w:sectPr", NS) is None:
        raise ValueError(
            "O documento oficial deve conter seções distintas para capa, "
            "páginas internas e contracapa."
        )

    client_replaced = False
    period_replaced = False
    for paragraph in body.findall("w:p", NS):
        text = _paragraph_text(paragraph).strip()
        text_nodes = paragraph.xpath(".//w:t", namespaces=NS)
        if not client_replaced and text == source_client:
            text_nodes[0].text = "{{CLIENT_NAME}}"
            for node in text_nodes[1:]:
                node.text = ""
            properties = paragraph.find("w:pPr", NS)
            style = None if properties is None else properties.find("w:pStyle", NS)
            if style is None:
                raise ValueError("O estilo do cliente na capa não foi localizado.")
            style.set(qn(W, "val"), "CoverClient")
            client_replaced = True
        if not period_replaced and MONTH_PATTERN.fullmatch(text):
            text_nodes[0].text = "{{PERIOD_LABEL}}"
            text_nodes[0].set(qn(XML, "space"), "preserve")
            for node in text_nodes[1:]:
                node.text = ""
            period_replaced = True

    if not client_replaced:
        raise ValueError(f"Cliente de origem não localizado na capa: {source_client}")
    if not period_replaced:
        raise ValueError("Mês/ano de origem não localizado na capa.")

    for paragraph in body.findall("w:p", NS):
        properties = paragraph.find("w:pPr", NS)
        if properties is None:
            properties = etree.Element(qn(W, "pPr"))
            paragraph.insert(0, properties)
        style = properties.find("w:pStyle", NS)
        if style is None:
            style = etree.Element(qn(W, "pStyle"))
            style.set(qn(W, "val"), "CoverBody")
            properties.insert(0, style)
        elif style.get(qn(W, "val")) == "Ttulo":
            style.set(qn(W, "val"), "CoverTitle")
        if properties.find("w:sectPr", NS) is not None:
            break

    body_children = list(body)
    internal_end_index = body_children.index(section_paragraphs[1])
    for node in body_children[internal_end_index + 1 :]:
        paragraphs = (
            [node]
            if node.tag == qn(W, "p")
            else node.xpath(".//w:p", namespaces=NS)
        )
        for paragraph in paragraphs:
            properties = paragraph.find("w:pPr", NS)
            if properties is None:
                properties = etree.Element(qn(W, "pPr"))
                paragraph.insert(0, properties)
            style = properties.find("w:pStyle", NS)
            if style is None:
                style = etree.Element(qn(W, "pStyle"))
                style.set(qn(W, "val"), "BackCoverBody")
                properties.insert(0, style)
    return etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )


def _patch_internal_header(payload: bytes, source_client: str) -> bytes:
    root = etree.fromstring(payload)
    matched = 0
    for paragraph in root.xpath(".//w:p", namespaces=NS):
        text_nodes = paragraph.xpath(".//w:t", namespaces=NS)
        client_indexes = [
            index
            for index, node in enumerate(text_nodes)
            if (node.text or "").strip() == source_client
        ]
        for client_index in client_indexes:
            text_nodes[client_index].text = "{{CLIENT_NAME}}"
            for node in text_nodes[client_index + 1 :]:
                node.text = ""
            matched += 1
    if matched == 0:
        raise ValueError(
            f"Cliente de origem não localizado no cabeçalho interno: {source_client}"
        )
    return etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )


def _patch_settings(payload: bytes) -> bytes:
    root = etree.fromstring(payload)
    update = root.find("w:updateFields", NS)
    if update is None:
        update = etree.Element(qn(W, "updateFields"))
        root.insert(0, update)
    update.set(qn(W, "val"), "true")
    return etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )


def _patch_styles(payload: bytes) -> bytes:
    root = etree.fromstring(payload)
    if not root.xpath('./w:style[@w:styleId="CoverClient"]', namespaces=NS):
        heading_one = root.xpath(
            './w:style[w:name[@w:val="heading 1"]]', namespaces=NS
        )
        if len(heading_one) != 1:
            raise ValueError("O estilo Heading 1 oficial não foi localizado.")
        cover_style = etree.fromstring(etree.tostring(heading_one[0]))
        cover_style.set(qn(W, "styleId"), "CoverClient")
        cover_style.attrib.pop(qn(W, "default"), None)
        name = cover_style.find("w:name", NS)
        if name is not None:
            name.set(qn(W, "val"), "Cover Client")
        paragraph_properties = cover_style.find("w:pPr", NS)
        if paragraph_properties is not None:
            for child_name in ("numPr", "outlineLvl"):
                child = paragraph_properties.find(f"w:{child_name}", NS)
                if child is not None:
                    paragraph_properties.remove(child)
        root.append(cover_style)

    if not root.xpath('./w:style[@w:styleId="CoverBody"]', namespaces=NS):
        normal = root.xpath(
            './w:style[@w:styleId="Normal"]', namespaces=NS
        )
        if len(normal) != 1:
            raise ValueError("O estilo Normal oficial não foi localizado.")
        cover_body = etree.fromstring(etree.tostring(normal[0]))
        cover_body.set(qn(W, "styleId"), "CoverBody")
        cover_body.attrib.pop(qn(W, "default"), None)
        name = cover_body.find("w:name", NS)
        if name is not None:
            name.set(qn(W, "val"), "Cover Body")
        root.append(cover_body)

    if not root.xpath('./w:style[@w:styleId="BackCoverBody"]', namespaces=NS):
        normal = root.xpath(
            './w:style[@w:styleId="Normal"]', namespaces=NS
        )
        if len(normal) != 1:
            raise ValueError("O estilo Normal oficial não foi localizado.")
        back_cover_body = etree.fromstring(etree.tostring(normal[0]))
        back_cover_body.set(qn(W, "styleId"), "BackCoverBody")
        back_cover_body.attrib.pop(qn(W, "default"), None)
        name = back_cover_body.find("w:name", NS)
        if name is not None:
            name.set(qn(W, "val"), "Back Cover Body")
        root.append(back_cover_body)

    if not root.xpath('./w:style[@w:styleId="CoverTitle"]', namespaces=NS):
        title = root.xpath('./w:style[@w:styleId="Ttulo"]', namespaces=NS)
        if len(title) != 1:
            raise ValueError("O estilo de título da capa oficial não foi localizado.")
        cover_title = etree.fromstring(etree.tostring(title[0]))
        cover_title.set(qn(W, "styleId"), "CoverTitle")
        cover_title.attrib.pop(qn(W, "default"), None)
        name = cover_title.find("w:name", NS)
        if name is not None:
            name.set(qn(W, "val"), "Cover Title")
        based_on = cover_title.find("w:basedOn", NS)
        if based_on is not None:
            based_on.set(qn(W, "val"), "CoverClient")
        root.append(cover_title)
    return etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )


def _patch_core_properties(payload: bytes) -> bytes:
    root = etree.fromstring(payload)
    namespaces = {
        "dc": "http://purl.org/dc/elements/1.1/",
        "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    }
    replacements = {
        "dc:title": "Template corporativo oficial do relatório Tenable",
        "dc:subject": "Capa, páginas internas e contracapa oficiais",
        "dc:creator": "ITProtect",
        "cp:lastModifiedBy": "ITProtect",
    }
    for xpath, value in replacements.items():
        nodes = root.xpath(f"./{xpath}", namespaces=namespaces)
        if nodes:
            nodes[0].text = value
    return etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )


def _patch_image_alt_text(payload: bytes) -> bytes:
    root = etree.fromstring(payload)
    changed = False
    for image in root.xpath(".//wp:docPr", namespaces=NS):
        if not (image.get("descr") or "").strip():
            name = (image.get("name") or "imagem").strip()
            image.set("descr", f"Elemento visual corporativo: {name}")
            changed = True
    if not changed:
        return payload
    return etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )


def build_template(source: Path, output: Path, source_client: str) -> Path:
    patches = {
        "word/document.xml": lambda payload: _patch_document(
            payload, source_client
        ),
        "word/header1.xml": lambda payload: _patch_internal_header(
            payload, source_client
        ),
        "word/settings.xml": _patch_settings,
        "word/styles.xml": _patch_styles,
        "docProps/core.xml": _patch_core_properties,
    }
    result = io.BytesIO()
    with ZipFile(source, "r") as source_zip, ZipFile(
        result, "w", ZIP_DEFLATED
    ) as output_zip:
        for info in source_zip.infolist():
            payload = source_zip.read(info.filename)
            patcher = patches.get(info.filename)
            if patcher is not None:
                payload = patcher(payload)
            if (
                info.filename.startswith("word/")
                and info.filename.endswith(".xml")
                and b"docPr" in payload
            ):
                payload = _patch_image_alt_text(payload)
            output_zip.writestr(info, payload)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result.getvalue())
    with ZipFile(output) as package:
        xml = "\n".join(
            package.read(name).decode("utf-8")
            for name in package.namelist()
            if name.endswith(".xml")
        )
    if source_client in xml:
        output.unlink(missing_ok=True)
        raise ValueError("O identificador do cliente de origem permaneceu no template.")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sanitiza o DOCX oficial para uso como template corporativo."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-client", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(build_template(args.source, args.output, args.source_client))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
