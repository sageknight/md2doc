#!/usr/bin/env python3
"""
postprocess_tables.py

All table styling for the ODT output lives here:
  - Cell styles (background, borders, padding, vertical alignment)
  - Paragraph styles (horizontal alignment, text color, font)

Both are injected into content.xml's <office:automatic-styles>, which
takes priority over any common style of the same name in styles.xml.

Usage:
    python postprocess_tables.py output.odt
    python postprocess_tables.py output.odt \
        --font "Liberation Sans" --body-size 10.5 \
        --text-color 2C3E50 \
        --header-bg 00174F --header-fg FFFFFF \
        --row-alt EDFDFD --border FFFFFF \
        --border-width 2.25
"""

import argparse
import shutil
import sys
import zipfile
from lxml import etree


TABLE_NS  = 'urn:oasis:names:tc:opendocument:xmlns:table:1.0'
OFFICE_NS = 'urn:oasis:names:tc:opendocument:xmlns:office:1.0'
STYLE_NS  = 'urn:oasis:names:tc:opendocument:xmlns:style:1.0'
FO_NS     = 'urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0'

TABLE         = f'{{{TABLE_NS}}}table'
TABLE_ROW     = f'{{{TABLE_NS}}}table-row'
TABLE_CELL    = f'{{{TABLE_NS}}}table-cell'
COVERED_CELL  = f'{{{TABLE_NS}}}covered-table-cell'
HEADER_ROWS   = f'{{{TABLE_NS}}}table-header-rows'
STYLE_NAME    = f'{{{TABLE_NS}}}style-name'

OFFICE_AUTO_STYLES = f'{{{OFFICE_NS}}}automatic-styles'
OFFICE_BODY        = f'{{{OFFICE_NS}}}body'

STYLE_STYLE        = f'{{{STYLE_NS}}}style'
STYLE_NAME_ATTR    = f'{{{STYLE_NS}}}name'
STYLE_FAMILY       = f'{{{STYLE_NS}}}family'
STYLE_DISPLAY_NAME = f'{{{STYLE_NS}}}display-name'
STYLE_VERT_ALIGN   = f'{{{STYLE_NS}}}vertical-align'
STYLE_FONT_NAME    = f'{{{STYLE_NS}}}font-name'

TC_PROPS = f'{{{STYLE_NS}}}table-cell-properties'
P_PROPS  = f'{{{STYLE_NS}}}paragraph-properties'
T_PROPS  = f'{{{STYLE_NS}}}text-properties'

FO_BG          = f'{{{FO_NS}}}background-color'
FO_BORDER      = f'{{{FO_NS}}}border'
FO_PADDING     = f'{{{FO_NS}}}padding'
FO_TEXT_ALIGN  = f'{{{FO_NS}}}text-align'
FO_MARGIN      = f'{{{FO_NS}}}margin'
FO_TEXT_INDENT = f'{{{FO_NS}}}text-indent'
FO_COLOR       = f'{{{FO_NS}}}color'
FO_FONT_WEIGHT = f'{{{FO_NS}}}font-weight'
FO_FONT_SIZE   = f'{{{FO_NS}}}font-size'


def _hex(c):
    return '#' + c.lstrip('#')


def _remove_existing(root, style_name):
    """Remove any style with this name so re-runs are idempotent."""
    for s in list(root.iter(STYLE_STYLE)):
        if s.get(STYLE_NAME_ATTR) == style_name:
            s.getparent().remove(s)


def _ensure_automatic_styles(root):
    auto = root.find(OFFICE_AUTO_STYLES)
    if auto is None:
        body = root.find(OFFICE_BODY)
        auto = etree.Element(OFFICE_AUTO_STYLES)
        if body is not None:
            body.addprevious(auto)
        else:
            root.append(auto)
    return auto


def _build_cell_style(parent, style_name, display_name,
                      bg_color, border_color, border_width):
    s = etree.SubElement(parent, STYLE_STYLE)
    s.set(STYLE_NAME_ATTR, style_name)
    s.set(STYLE_DISPLAY_NAME, display_name)
    s.set(STYLE_FAMILY, 'table-cell')

    tc = etree.SubElement(s, TC_PROPS)
    tc.set(FO_BG, _hex(bg_color))
    tc.set(FO_BORDER, f'{border_width}pt solid {_hex(border_color)}')
    tc.set(FO_PADDING, '4pt')
    tc.set(STYLE_VERT_ALIGN, 'middle')
    return s


def _build_paragraph_style(parent, style_name, display_name,
                           text_color, font_weight, font_size, font_name):
    s = etree.SubElement(parent, STYLE_STYLE)
    s.set(STYLE_NAME_ATTR, style_name)
    s.set(STYLE_DISPLAY_NAME, display_name)
    s.set(STYLE_FAMILY, 'paragraph')

    p = etree.SubElement(s, P_PROPS)
    p.set(FO_TEXT_ALIGN, 'center')
    p.set(FO_MARGIN, '0pt')
    p.set(FO_TEXT_INDENT, '0in')
    p.set(FO_BG, 'transparent')

    t = etree.SubElement(s, T_PROPS)
    t.set(FO_COLOR, _hex(text_color))
    t.set(FO_FONT_WEIGHT, font_weight)
    t.set(FO_FONT_SIZE, f'{font_size}pt')
    t.set(STYLE_FONT_NAME, font_name)
    return s


def postprocess_odt_tables(odt_path, opts):
    tmp_path = odt_path + '.tmp'

    with zipfile.ZipFile(odt_path, 'r') as zin:
        all_files = {name: zin.read(name) for name in zin.namelist()}

    root = etree.fromstring(all_files['content.xml'])
    auto = _ensure_automatic_styles(root)

    # Remove prior definitions so the script is idempotent.
    for name in (
        'TableHeaderCell', 'TableRowOdd', 'TableRowEven',
        'Table_20_Heading', 'Table_20_Contents',
    ):
        _remove_existing(root, name)

    # --- Cell styles (background, border, padding, vertical align) ---
    _build_cell_style(auto, 'TableHeaderCell', 'Table Header Cell',
                      opts.header_bg, opts.border, opts.border_width)
    _build_cell_style(auto, 'TableRowOdd', 'Table Row Odd',
                      'FFFFFF', opts.border, opts.border_width)
    _build_cell_style(auto, 'TableRowEven', 'Table Row Even',
                      opts.row_alt, opts.border, opts.border_width)

    # --- Paragraph styles (horizontal align, text color, font) ---
    _build_paragraph_style(auto, 'Table_20_Heading', 'Table Heading',
                           opts.header_fg, 'bold',
                           opts.body_size, opts.font)
    _build_paragraph_style(auto, 'Table_20_Contents', 'Table Contents',
                           opts.text_color, 'normal',
                           opts.body_size, opts.font)

    # --- Walk every table and reassign cell styles ---
    tables_processed = 0
    cells_styled = 0

    for table in root.iter(TABLE):
        tables_processed += 1

        header_rows = set()
        for hdr in table.iter(HEADER_ROWS):
            for row in hdr.findall(TABLE_ROW):
                header_rows.add(row)

        body_index = 0
        for row in table.iter(TABLE_ROW):
            if row in header_rows:
                style = 'TableHeaderCell'
            else:
                body_index += 1
                style = 'TableRowOdd' if (body_index % 2 == 1) else 'TableRowEven'

            for cell in row.findall(TABLE_CELL):
                cell.set(STYLE_NAME, style)
                cells_styled += 1
            for cell in row.findall(COVERED_CELL):
                cell.set(STYLE_NAME, style)

    all_files['content.xml'] = etree.tostring(
        root, xml_declaration=True, encoding='UTF-8'
    )

    # ODF requires the uncompressed ``mimetype`` entry to be the first ZIP
    # member. Preserve that packaging rule while compressing the remaining files.
    with zipfile.ZipFile(tmp_path, 'w') as zout:
        if 'mimetype' in all_files:
            mimetype_info = zipfile.ZipInfo('mimetype')
            mimetype_info.compress_type = zipfile.ZIP_STORED
            zout.writestr(mimetype_info, all_files.pop('mimetype'))
        for name, content in all_files.items():
            zout.writestr(name, content, compress_type=zipfile.ZIP_DEFLATED)

    shutil.move(tmp_path, odt_path)

    print(f"  ✓ Processed {tables_processed} table(s), styled {cells_styled} cell(s)")
    print(f"  ✓ Wrote {odt_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Apply custom cell and paragraph styles to tables in an ODT."
    )
    parser.add_argument("odt_file")

    # Colors
    parser.add_argument("--header-bg",    default="00174F")
    parser.add_argument("--header-fg",    default="FFFFFF")
    parser.add_argument("--row-alt",      default="EDFDFD")
    parser.add_argument("--border",       default="FFFFFF")
    parser.add_argument("--text-color",   default="2C3E50")

    # Typography
    parser.add_argument("--font",         default="Liberation Sans")
    parser.add_argument("--body-size",    type=float, default=10.5)

    # Border
    parser.add_argument("--border-width", type=float, default=1.5)

    args = parser.parse_args()

    try:
        postprocess_odt_tables(args.odt_file, args)
    except FileNotFoundError:
        print(f"Error: file not found: {args.odt_file}", file=sys.stderr)
        sys.exit(1)
    except zipfile.BadZipFile:
        print(f"Error: not a valid ODT (zip) file: {args.odt_file}", file=sys.stderr)
        sys.exit(1)
    except KeyError:
        print("Error: ODT is missing content.xml", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()