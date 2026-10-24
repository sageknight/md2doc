#!/usr/bin/env python3
"""Build PDF, DOCX, ODT, and/or HTML documents from Markdown with Pandoc."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable

FORMATS = ("pdf", "docx", "odt", "html")
REFERENCE_FILES = {
    "pdf": "template.typ",
    "docx": "custom-reference.docx",
    "odt": "custom-reference.odt",
    "html": "styles.css",
}


def log(message: str) -> None:
    print(message, file=sys.stderr)


def fail(message: str, code: int = 2) -> "NoReturn":
    log(f"Error: {message}")
    raise SystemExit(code)


# ---------------------------------------------------------------- format args

def parse_formats(tokens: list[str]) -> tuple[list[str], list[str]]:
    selected: list[str] = []
    remaining: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--formats":
            if i + 1 >= len(tokens):
                fail("--formats requires a comma-separated value.")
            values = tokens[i + 1].split(",")
            i += 2
        elif token.startswith("--formats="):
            values = token.split("=", 1)[1].split(",")
            i += 1
        elif token == "--all" or token.startswith("--all,"):
            values = token[2:].split(",")
            i += 1
        elif token.startswith("--") and token[2:].split("=", 1)[0].split(",")[0] in FORMATS:
            head = token[2:]
            if "=" in head:
                name_part, value_part = head.split("=", 1)
                values = name_part.split(",")
                remaining.append("--output=" + value_part)
            else:
                values = head.split(",")
            i += 1
        else:
            remaining.append(token)
            i += 1
            continue

        for value in values:
            name = value.strip().lower().lstrip("-")
            if name == "all":
                selected.extend(FORMATS)
            elif name in FORMATS:
                selected.append(name)
            elif name:
                fail(f"Unknown format '{value}'. Choose from: {', '.join(FORMATS)}, all.")

    return [fmt for fmt in FORMATS if fmt in selected], remaining


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build PDF, DOCX, ODT, and/or HTML files from Markdown.",
    )
    parser.add_argument("input", nargs="?", help="Markdown file, or '-' for standard input")
    parser.add_argument("-i", "--input-file", dest="input_option")
    parser.add_argument("-o", "--output")
    parser.add_argument(
        "--output-dir", default="output",
        help="Base output directory; each Markdown file gets its own subfolder "
             "named after it (default: output)",
    )
    parser.add_argument("--cwd")
    parser.add_argument("--design-system-script", default="build_design_system.py")
    parser.add_argument("--mermaid-render-script", default="render_mermaid.py")
    parser.add_argument("--mermaid-config", default="mermaid_config.json")
    parser.add_argument("--mmdc")
    parser.add_argument("--pandoc", default="pandoc")
    parser.add_argument("--docx-table-postprocessor", default="postprocess_docx_tables.py")
    parser.add_argument("--odt-table-postprocessor")
    parser.add_argument("--citeproc", action="store_true")
    parser.add_argument("--bibliography", action="append", default=[])
    parser.add_argument("--csl")
    parser.add_argument("--pandoc-arg", action="append", default=[])
    parser.add_argument("--resource-path", action="append", default=[])
    parser.add_argument(
        "--force", action="store_true",
        help="Force regeneration of reference files and Mermaid images",
    )
    parser.add_argument("--no-design-system", action="store_true")
    parser.add_argument("--version", action="version", version="%(prog)s 1.0")
    return parser


# ---------------------------------------------------------------- helpers

def resolve_from(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def run_command(command: list[str], *, cwd: Path | None = None, label: str = "command") -> None:
    log("+ " + subprocess.list2cmdline(command))
    try:
        subprocess.run(command, cwd=str(cwd) if cwd else None, check=True)
    except FileNotFoundError:
        fail(f"Could not start {label}: executable not found ('{command[0]}').")
    except subprocess.CalledProcessError as exc:
        fail(f"{label} failed with exit code {exc.returncode}.", exc.returncode or 1)


def has_mermaid(text: str) -> bool:
    return bool(re.search(
        r"(?im)^[ \t]*(`{3,}|~{3,})[ \t]*mermaid(?:[ \t]+[^\r\n]*)?[ \t]*$",
        text,
    ))


def pandoc_ast_contains_table(node: object) -> bool:
    if isinstance(node, dict):
        if node.get("t") == "Table":
            return True
        return any(pandoc_ast_contains_table(value) for value in node.values())
    if isinstance(node, list):
        return any(pandoc_ast_contains_table(value) for value in node)
    return False


def markdown_has_tables(pandoc: str, source_text: str, cwd: Path) -> bool:
    try:
        result = subprocess.run(
            [pandoc, "--from=markdown", "--to=json"],
            input=source_text,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd),
            check=False,
        )
    except FileNotFoundError:
        fail(f"Could not start Pandoc ('{pandoc}') while checking Markdown for tables.")

    if result.returncode != 0:
        detail = result.stderr.strip()
        suffix = f"\n{detail}" if detail else ""
        fail(
            f"Pandoc could not parse the Markdown while checking for tables.{suffix}",
            result.returncode or 1,
        )
    try:
        ast = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        fail(f"Pandoc returned invalid JSON while checking for tables: {exc}")
    return pandoc_ast_contains_table(ast)


def resolve_table_postprocessor(
    project_dir: Path,
    explicit: str | None,
    candidates: tuple[str, ...],
    format_name: str,
) -> Path:
    if explicit:
        path = resolve_from(project_dir, explicit)
        if path.is_file():
            return path
        fail(f"{format_name.upper()} table postprocessor not found: {path}")

    for name in candidates:
        path = project_dir / name
        if path.is_file():
            return path.resolve()
    choices = " or ".join(repr(name) for name in candidates)
    fail(
        f"The Markdown contains a table and {format_name.upper()} output was selected, "
        f"but the table postprocessor was not found ({choices}). "
        f"Use --{format_name}-table-postprocessor to specify its path."
    )


def run_table_postprocessor(
    script: Path,
    document_path: str,
    *,
    cwd: Path,
    keep_stdout_clean: bool = False,
) -> None:
    command = [sys.executable, str(script), document_path]
    if not keep_stdout_clean:
        run_command(command, cwd=cwd, label=f"{script.name}")
        return

    log("+ " + subprocess.list2cmdline(command))
    try:
        result = subprocess.run(
            command, cwd=str(cwd),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", check=False,
        )
    except FileNotFoundError:
        fail(f"Could not start table postprocessor: {script}")
    if result.stdout.strip():
        log(result.stdout.rstrip())
    if result.stderr.strip():
        log(result.stderr.rstrip())
    if result.returncode != 0:
        fail(f"{script.name} failed with exit code {result.returncode}.", result.returncode or 1)


def has_inline_code(text: str) -> bool:
    return bool(re.search(r"(?<!\\)(`+)(?!`).+?(?<!`)\1(?!`)", text, re.DOTALL))


def has_definition_lists(text: str) -> bool:
    return bool(re.search(r"(?m)^[ \t]{0,3}:[ \t]+\S", text))


def has_task_lists(text: str) -> bool:
    return bool(re.search(r"(?m)^[ \t]*[-+*][ \t]+\[[ xX]\][ \t]+\S", text))


def move_reference_files(formats: Iterable[str], project_dir: Path, ref_dir: Path) -> None:
    ref_dir.mkdir(parents=True, exist_ok=True)
    for fmt in formats:
        name = REFERENCE_FILES[fmt]
        source = project_dir / name
        destination = ref_dir / name
        if source.exists():
            if source.resolve() != destination.resolve():
                shutil.move(str(source), str(destination))
        elif not destination.exists():
            fail(
                f"Reference file '{name}' was not generated and does not exist "
                f"at '{destination}'."
            )


def builder_format_arg(formats: list[str]) -> str:
    return "--" + ",".join(formats)


def pandoc_command(
    pandoc: str,
    fmt: str,
    markdown_path: Path,
    output_path: str,
    ref_dir: Path,
    source_text: str,
    args: argparse.Namespace,
    input_parent: Path,
) -> list[str]:
    command = [pandoc, str(markdown_path), "-o", output_path]
    if fmt in ("pdf", "docx", "odt", "html"):
        command.append("--toc")
    if fmt == "pdf":
        command.extend([
            "--to=typst", "--pdf-engine=typst",
            f"--template={ref_dir / 'template.typ'}",
        ])
    elif fmt == "docx":
        command.append(f"--reference-doc={ref_dir / 'custom-reference.docx'}")
        if has_inline_code(source_text):
            command.append("--lua-filter=inline-code.lua")
        if has_definition_lists(source_text):
            command.append("--lua-filter=definition-list-combined.lua")
    elif fmt == "odt":
        command.extend([
            "-M", "toc-title=Table of Contents",
            f"--reference-doc={ref_dir / 'custom-reference.odt'}",
        ])
        if has_definition_lists(source_text):
            command.append("--lua-filter=definition-list-combined.lua")
        if has_task_lists(source_text):
            command.append("--lua-filter=task-list-fix.lua")
    elif fmt == "html":
        if output_path == "-":
            css_path = ref_dir / "styles.css"
        else:
            html_parent = Path(output_path).expanduser().resolve().parent
            css_path = Path(os.path.relpath(ref_dir / "styles.css", start=html_parent))
        command.extend(["--standalone", f"--css={css_path.as_posix()}"])

    if args.citeproc:
        command.append("--citeproc")
    for bibliography in args.bibliography:
        command.append(f"--bibliography={resolve_from(input_parent, bibliography)}")
    if args.csl:
        command.append(f"--csl={resolve_from(input_parent, args.csl)}")

    resource_paths = [
        markdown_path.parent,
        ref_dir.parent,
        input_parent,
        *[resolve_from(input_parent, p) for p in args.resource_path],
    ]
    command.append("--resource-path=" + os.pathsep.join(
        str(p) for p in dict.fromkeys(resource_paths)
    ))
    command.extend(args.pandoc_arg)
    return command


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    formats, raw = parse_formats(raw)
    parser = build_parser()
    args = parser.parse_args(raw)
    if not formats:
        parser.error("select at least one format, e.g. --pdf,docx or --all")
    if args.input and args.input_option:
        parser.error("provide the input either positionally or with -i/--input-file, not both")
    input_arg = args.input_option or args.input
    if not input_arg:
        parser.error("an input Markdown file is required (use '-' for standard input)")
    if args.output == "-" and len(formats) != 1:
        parser.error("-o - (stdout) can only be used with one output format")

    project_dir = Path(args.cwd).expanduser().resolve() if args.cwd else Path.cwd().resolve()
    builder = resolve_from(project_dir, args.design_system_script)
    render_script = resolve_from(project_dir, args.mermaid_render_script)
    config_path = resolve_from(project_dir, args.mermaid_config)

    stdin_mode = input_arg == "-"
    if stdin_mode:
        source_text = sys.stdin.read()
        source_path = None
        source_stem = "document"
        input_parent = project_dir
    else:
        source_path = resolve_from(project_dir, input_arg)
        if not source_path.is_file():
            fail(f"Input Markdown file not found: {source_path}")
        source_text = source_path.read_text(encoding="utf-8-sig")
        source_stem = source_path.stem
        input_parent = source_path.parent

    markdown_contains_tables = False
    table_postprocessors: dict[str, Path] = {}
    if {"docx", "odt"}.intersection(formats):
        markdown_contains_tables = markdown_has_tables(args.pandoc, source_text, project_dir)
        if markdown_contains_tables and "docx" in formats:
            table_postprocessors["docx"] = resolve_table_postprocessor(
                project_dir, args.docx_table_postprocessor,
                ("postprocess_docx_tables.py",), "docx",
            )
        if markdown_contains_tables and "odt" in formats:
            table_postprocessors["odt"] = resolve_table_postprocessor(
                project_dir, args.odt_table_postprocessor,
                ("post_process_tables.py", "postprocess_tables.py"), "odt",
            )

    # ---- Output layout -------------------------------------------------------
    base_dir = resolve_from(project_dir, args.output_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    doc_dir = base_dir / source_stem
    doc_dir.mkdir(parents=True, exist_ok=True)
    ref_dir = base_dir / "reference_files"
    ref_dir.mkdir(parents=True, exist_ok=True)

    if args.output == "-":
        def final_output_path(fmt: str) -> str:
            return "-"
    elif args.output and len(formats) == 1:
        candidate = resolve_from(project_dir, args.output)
        if candidate.exists() and candidate.is_dir():
            explicit = candidate / f"{source_stem}.{formats[0]}"
        else:
            explicit = candidate
        def final_output_path(fmt: str) -> str:
            return str(explicit)
    elif args.output:
        multi_dir = resolve_from(project_dir, args.output)
        multi_dir.mkdir(parents=True, exist_ok=True)
        def final_output_path(fmt: str) -> str:
            return str(multi_dir / f"{source_stem}.{fmt}")
    else:
        def final_output_path(fmt: str) -> str:
            return str(doc_dir / f"{source_stem}.{fmt}")

    # ---- Reference-file cache ------------------------------------------------
    missing_refs = [
        fmt for fmt in formats if not (ref_dir / REFERENCE_FILES[fmt]).exists()
    ]
    rebuild_refs = args.force or bool(missing_refs)
    if rebuild_refs:
        if args.no_design_system and missing_refs:
            fail(
                "--no-design-system selected, but reference file(s) missing: "
                + ", ".join(REFERENCE_FILES[f] for f in missing_refs)
            )
        if not args.no_design_system:
            if not builder.is_file():
                fail(
                    f"Design-system builder not found: {builder} "
                    f"(use --design-system-script to specify it)."
                )
            run_command(
                [sys.executable, str(builder), builder_format_arg(formats)],
                cwd=project_dir, label="build_design_system.py",
            )
            move_reference_files(formats, project_dir, ref_dir)
    else:
        log(f"Using cached reference files in {ref_dir}")

    # ---- Mermaid -------------------------------------------------------------
    svg_formats = {"pdf", "docx", "html"}.intersection(formats)
    has_svg_format = bool(svg_formats)
    has_pdf_format = "odt" in formats
    has_mermaid_blocks = has_mermaid(source_text)

    need_svg = has_mermaid_blocks and has_svg_format
    need_pdf = has_mermaid_blocks and has_pdf_format

    images_dir = doc_dir / f"{source_stem}-images"
    svg_md_path = doc_dir / f"{source_stem}-svg.md"
    pdf_md_path = doc_dir / f"{source_stem}-pdf.md"

    temp_stdin: Path | None = None
    if source_path is None:
        fd, name = tempfile.mkstemp(
            prefix="publish-stdin-", suffix=".md", dir=str(doc_dir)
        )
        os.close(fd)
        temp_stdin = Path(name)
        temp_stdin.write_text(source_text, encoding="utf-8")
        source_path = temp_stdin

    try:
        if need_svg or need_pdf:
            if not config_path.is_file():
                fail(f"Mermaid config file not found: {config_path}")
            if not render_script.is_file():
                fail(f"Mermaid renderer not found: {render_script}")

            render_args: list[str] = [
                sys.executable, str(render_script),
                str(source_path),
                "--output-dir", str(images_dir),
                "--config", str(config_path),
            ]
            if need_svg:
                render_args.append("--svg")
                render_args.extend(["--svg-markdown", str(svg_md_path)])
            if need_pdf:
                render_args.append("--pdf")
                render_args.extend(["--pdf-markdown", str(pdf_md_path)])
            if args.mmdc:
                render_args.extend(["--mmdc", args.mmdc])
            if args.force:
                render_args.append("--force")

            run_command(render_args, cwd=project_dir, label="render_mermaid.py")

        # ---- Per-format Pandoc invocation -----------------------------------
        for fmt in formats:
            output_path = final_output_path(fmt)

            if fmt == "odt" and need_pdf and pdf_md_path.is_file():
                markdown_path = pdf_md_path
            elif fmt in svg_formats and need_svg and svg_md_path.is_file():
                markdown_path = svg_md_path
            else:
                markdown_path = source_path

            stdout_temp: Path | None = None
            pandoc_output_path = output_path
            should_postprocess_tables = (
                fmt in table_postprocessors and markdown_contains_tables
            )
            if output_path == "-" and should_postprocess_tables:
                fd, temp_name = tempfile.mkstemp(
                    prefix=f"publish-{fmt}-", suffix=f".{fmt}", dir=str(doc_dir)
                )
                os.close(fd)
                stdout_temp = Path(temp_name)
                pandoc_output_path = str(stdout_temp)

            try:
                command = pandoc_command(
                    args.pandoc, fmt, markdown_path, pandoc_output_path,
                    ref_dir, source_text, args, input_parent,
                )
                run_command(command, cwd=doc_dir, label=f"Pandoc ({fmt})")

                if should_postprocess_tables:
                    run_table_postprocessor(
                        table_postprocessors[fmt],
                        pandoc_output_path,
                        cwd=project_dir,
                        keep_stdout_clean=(output_path == "-"),
                    )

                if stdout_temp is not None:
                    with stdout_temp.open("rb") as stream:
                        shutil.copyfileobj(stream, sys.stdout.buffer)
                    sys.stdout.buffer.flush()
                log(f"Created {output_path}")
            finally:
                if stdout_temp is not None:
                    try:
                        stdout_temp.unlink()
                    except OSError:
                        pass
    finally:
        if temp_stdin:
            try:
                temp_stdin.unlink()
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())