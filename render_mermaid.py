#!/usr/bin/env python3
"""Render Markdown Mermaid blocks to SVG and/or PDF with mmdc.

Writes the following under --output-dir:
    mmd_files/   image1.mmd, image2.mmd, ...
    svg_images/  image1.svg, image1.sig, ...
    pdf_images/  image1.pdf, image1.sig, ...

Each .sig file stores the SHA-256 prefix of the .mmd source that produced the
corresponding image. On the next run, if the hash matches, that image is
skipped. Editing one block therefore re-renders only that block.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
import shutil
from pathlib import Path


FENCE_OPEN = re.compile(r"^([ \t]*)(`{3,}|~{3,})(.*)$")
ACC_TITLE_RE = re.compile(r"^\s*accTitle\s*:\s*(.+?)\s*$", re.IGNORECASE)
ACC_DESCR_RE = re.compile(r"^\s*accDescr\s*:\s*(.+?)\s*$", re.IGNORECASE)
ACC_DESCR_BLOCK_START = re.compile(r"^\s*accDescr\s*\{\s*$", re.IGNORECASE)
ACC_DESCR_BLOCK_END = re.compile(r"^\s*\}\s*$")


def find_mmdc() -> str:
    for name in ("mmdc.cmd", "mmdc.exe", "mmdc"):
        path = shutil.which(name)
        if path:
            return path
    raise FileNotFoundError(
        "Could not find Mermaid CLI (mmdc). "
        "Install it or specify its path using --mmdc."
    )


def extract_mermaid_accessibility(code: str) -> tuple[str, str]:
    """Return (accTitle, accDescr) parsed from the diagram body."""
    title = ""
    descr_lines: list[str] = []
    in_descr_block = False

    for line in code.splitlines():
        stripped = line.strip()

        if in_descr_block:
            if ACC_DESCR_BLOCK_END.match(stripped):
                in_descr_block = False
            else:
                descr_lines.append(stripped)
            continue

        if ACC_DESCR_BLOCK_START.match(stripped):
            in_descr_block = True
            continue

        m = ACC_TITLE_RE.match(stripped)
        if m:
            title = m.group(1).strip().strip('"').strip("'")
            continue

        m = ACC_DESCR_RE.match(stripped)
        if m:
            descr_lines = [m.group(1).strip().strip('"').strip("'")]
            continue

    descr = " ".join(part for part in descr_lines if part)
    return title, descr


def extract_mermaid_blocks(text: str) -> list[tuple[int, str]]:
    """Return (line number, Mermaid source) pairs from fenced code blocks."""
    lines = text.splitlines(keepends=True)
    diagrams: list[tuple[int, str]] = []
    i = 0

    while i < len(lines):
        match = FENCE_OPEN.match(lines[i].rstrip("\r\n"))
        if not match:
            i += 1
            continue

        indent, fence, info = match.groups()
        language = info.strip().split(maxsplit=1)[0].lower() if info.strip() else ""

        if language != "mermaid":
            i += 1
            marker = fence[0]
            while i < len(lines):
                candidate = lines[i].strip()
                if (
                    candidate.startswith(marker * len(fence))
                    and set(candidate) == {marker}
                ):
                    i += 1
                    break
                i += 1
            continue

        start_line = i + 1
        i += 1
        body: list[str] = []

        marker = fence[0]
        while i < len(lines):
            candidate = lines[i].strip()
            if (
                candidate.startswith(marker * len(fence))
                and set(candidate) == {marker}
            ):
                break
            body.append(lines[i])
            i += 1

        if i >= len(lines):
            raise ValueError(
                f"Unclosed Mermaid code fence starting at line {start_line}"
            )

        diagrams.append((start_line, "".join(body).rstrip() + "\n"))
        i += 1

    return diagrams


def compute_block_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]


def is_cached(output_file: Path, sig_file: Path, expected_hash: str) -> bool:
    if not output_file.is_file() or output_file.stat().st_size == 0:
        return False
    if not sig_file.is_file():
        return False
    return sig_file.read_text(encoding="utf-8").strip() == expected_hash


def render_block(
    mmdc: str,
    mmd_file: Path,
    output_file: Path,
    config: Path | None,
    cwd: Path,
) -> bool:
    command = [mmdc, "-i", str(mmd_file), "-o", str(output_file)]
    if config:
        command.extend(["-c", str(config)])
    try:
        subprocess.run(command, cwd=str(cwd), check=True)
    except FileNotFoundError:
        print(f"Error: Could not find '{mmdc}'.", file=sys.stderr)
        return False
    except subprocess.CalledProcessError as exc:
        print(
            f"Error rendering {mmd_file.name} (exit code {exc.returncode}).",
            file=sys.stderr,
        )
        return False
    return output_file.is_file() and output_file.stat().st_size > 0


def transform_markdown(
    text: str,
    image_paths: list[str],
    alt_texts: list[str],
    titles: list[str],
) -> str:
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    i = 0
    idx = 0

    while i < len(lines):
        match = FENCE_OPEN.match(lines[i].rstrip("\r\n"))
        if not match:
            output.append(lines[i])
            i += 1
            continue

        indent, fence, info = match.groups()
        language = info.strip().split(maxsplit=1)[0].lower() if info.strip() else ""

        if language != "mermaid":
            output.append(lines[i])
            i += 1
            marker = fence[0]
            while i < len(lines):
                output.append(lines[i])
                candidate = lines[i].strip()
                i += 1
                if (
                    candidate.startswith(marker * len(fence))
                    and set(candidate) == {marker}
                ):
                    break
            continue

        marker = fence[0]
        i += 1
        while i < len(lines):
            candidate = lines[i].strip()
            if (
                candidate.startswith(marker * len(fence))
                and set(candidate) == {marker}
            ):
                i += 1
                break
            i += 1

        path = image_paths[idx]
        alt = alt_texts[idx]
        title = titles[idx]
        idx += 1

        title_part = f' "{title}"' if title else ""
        output.append(f'{indent}![{alt}]({path}{title_part})''{width=6.5in}\n')

    return "".join(output)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render Markdown Mermaid blocks to SVG and/or PDF with mmdc."
    )
    parser.add_argument("input", type=Path, help="Source Markdown file")
    parser.add_argument(
        "--output-dir", type=Path, required=True,
        help="Directory for generated files (typically output/{stem}-images).",
    )
    parser.add_argument(
        "--svg-markdown", type=Path,
        help="Processed Markdown with SVG references (requires --svg).",
    )
    parser.add_argument(
        "--pdf-markdown", type=Path,
        help="Processed Markdown with PDF references (requires --pdf).",
    )
    parser.add_argument("--svg", action="store_true", help="Generate SVG images.")
    parser.add_argument("--pdf", action="store_true", help="Generate PDF images.")
    parser.add_argument("--config", type=Path, help="Mermaid config JSON file.")
    parser.add_argument("--mmdc", default=None, help="Path to mmdc (auto-detect by default).")
    parser.add_argument(
        "--force", action="store_true",
        help="Ignore per-block signatures and re-render all images.",
    )
    args = parser.parse_args()

    if not args.svg and not args.pdf:
        parser.error("specify --svg and/or --pdf.")
    if args.svg_markdown and not args.svg:
        parser.error("--svg-markdown requires --svg.")
    if args.pdf_markdown and not args.pdf:
        parser.error("--pdf-markdown requires --pdf.")

    if args.mmdc is None:
        try:
            args.mmdc = find_mmdc()
        except FileNotFoundError as exc:
            parser.error(str(exc))

    source = args.input.resolve()
    if not source.is_file():
        parser.error(f"Input file not found: {source}")

    text = source.read_text(encoding="utf-8")
    diagrams = extract_mermaid_blocks(text)
    if not diagrams:
        print("No Mermaid code blocks found.", file=sys.stderr)
        return 1

    output_dir = args.output_dir.resolve()
    mmd_dir = output_dir / "mmd_files"
    svg_dir = output_dir / "svg_images"
    pdf_dir = output_dir / "pdf_images"
    mmd_dir.mkdir(parents=True, exist_ok=True)
    if args.svg:
        svg_dir.mkdir(parents=True, exist_ok=True)
    if args.pdf:
        pdf_dir.mkdir(parents=True, exist_ok=True)

    config = args.config.resolve() if args.config else None

    svg_md_dir = args.svg_markdown.resolve().parent if args.svg_markdown else None
    pdf_md_dir = args.pdf_markdown.resolve().parent if args.pdf_markdown else None

    svg_paths: list[str] = []
    pdf_paths: list[str] = []
    alt_texts: list[str] = []
    titles: list[str] = []

    rendered_svg = skipped_svg = 0
    rendered_pdf = skipped_pdf = 0

    for index, (line_number, code) in enumerate(diagrams, start=1):
        stem = f"image{index}"
        mmd_file = mmd_dir / f"{stem}.mmd"
        mmd_file.write_text(code, encoding="utf-8")

        acc_title, acc_descr = extract_mermaid_accessibility(code)
        if acc_title and acc_descr:
            alt_texts.append(f"{acc_title}: {acc_descr}")
        else:
            alt_texts.append(acc_descr or acc_title or "")
        titles.append(acc_title)

        block_hash = compute_block_hash(code)

        if args.svg:
            svg_file = svg_dir / f"{stem}.svg"
            svg_sig = svg_dir / f"{stem}.sig"
            if not args.force and is_cached(svg_file, svg_sig, block_hash):
                skipped_svg += 1
            else:
                print(f"[{index}/{len(diagrams)}] rendering {stem}.svg (line {line_number})")
                if not render_block(args.mmdc, mmd_file, svg_file, config, mmd_dir):
                    return 1
                svg_sig.write_text(block_hash, encoding="utf-8")
                rendered_svg += 1
            if svg_md_dir is not None:
                rel = Path(
                    __import__("os").path.relpath(svg_file, start=svg_md_dir)
                ).as_posix()
                svg_paths.append(rel)

        if args.pdf:
            pdf_file = pdf_dir / f"{stem}.pdf"
            pdf_sig = pdf_dir / f"{stem}.sig"
            if not args.force and is_cached(pdf_file, pdf_sig, block_hash):
                skipped_pdf += 1
            else:
                print(f"[{index}/{len(diagrams)}] rendering {stem}.pdf (line {line_number})")
                if not render_block(args.mmdc, mmd_file, pdf_file, config, mmd_dir):
                    return 1
                pdf_sig.write_text(block_hash, encoding="utf-8")
                rendered_pdf += 1
            if pdf_md_dir is not None:
                rel = Path(
                    __import__("os").path.relpath(pdf_file, start=pdf_md_dir)
                ).as_posix()
                pdf_paths.append(rel)

    if args.svg_markdown:
        rendered = transform_markdown(text, svg_paths, alt_texts, titles)
        args.svg_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.svg_markdown.write_text(rendered, encoding="utf-8", newline="")

    if args.pdf_markdown:
        rendered = transform_markdown(text, pdf_paths, alt_texts, titles)
        args.pdf_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.pdf_markdown.write_text(rendered, encoding="utf-8", newline="")

    print()
    if args.svg:
        print(f"SVG: {rendered_svg} rendered, {skipped_svg} cached → {svg_dir}")
    if args.pdf:
        print(f"PDF: {rendered_pdf} rendered, {skipped_pdf} cached → {pdf_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())