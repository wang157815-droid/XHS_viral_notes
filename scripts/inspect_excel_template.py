"""
Excel 模板结构抽取器 (2026-04-20 for phase-4.3pre 画布重构)

把一份 xlsx 模板展开成 Markdown 结构描述,便于 LLM/人类理解模板骨架。

抽取维度:
- 基础: sheet 名 / 总行列 / 冻结窗格
- 合并单元格: 所有合并区域(识别多级标题)
- 表头: 前 N 行作为候选表头(支持多级)
- 列类型: 基于前 K 行样本推断(text/number/date/percent/formula)
- 数据验证: 下拉菜单的枚举值
- 条件格式: 简要描述
- 公式: 第一次出现的公式字符串
- 批注: 单元格 comments(常含字段说明)
- 前 N 行示例数据
- **嵌入图片**: 抽出到磁盘 + Markdown 里直接引用(2026-04-20 新增)

用法:
    python scripts/inspect_excel_template.py docs/templates/xxx.xlsx
    python scripts/inspect_excel_template.py docs/templates/xxx.xlsx --out docs/templates/xxx_spec.md
    python scripts/inspect_excel_template.py docs/templates/  # 批量处理目录下所有 xlsx
    python scripts/inspect_excel_template.py docs/templates/xxx.xlsx --no-images  # 跳过图片抽取

嵌入图片默认抽到 `<输出目录>/images/`:
    docs/templates/xxx.xlsx → docs/templates/xxx.spec.md + docs/templates/images/xxx_sheet1_img01.png
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


try:
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.worksheet import Worksheet
except ImportError:
    print("[inspect_excel_template] 缺少 openpyxl,请先 pip install openpyxl", file=sys.stderr)
    sys.exit(1)


# ----------------------------------------------------------------------
# 图片抽取
# ----------------------------------------------------------------------


_IMG_EXT_BY_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"RIFF", "webp"),  # webp 以 RIFF 开头,简单够用
    (b"BM", "bmp"),
]


def _sniff_ext(data: bytes) -> str:
    for magic, ext in _IMG_EXT_BY_MAGIC:
        if data.startswith(magic):
            return ext
    return "bin"


def extract_sheet_images(
    ws: Worksheet,
    *,
    sheet_safe_name: str,
    xlsx_stem: str,
    images_dir: Path,
) -> List[Dict[str, Any]]:
    """抽取 sheet 内嵌入的图片到磁盘,返回每张图片的 metadata。

    openpyxl 读 xlsx 时会把图片加载到 `ws._images` 列表,每个 Image 对象有:
    - `_data()` 可调用,返回原始字节(png/jpg/...)
    - `anchor` 定位对象,有 `_from.col/row` 给出左上角位置

    返回:
        [{"filename": str, "rel_path": str, "anchor_cell": str, "size_kb": int, "ext": str}, ...]
    """
    results: List[Dict[str, Any]] = []
    images = getattr(ws, "_images", None) or []
    if not images:
        return results

    images_dir.mkdir(parents=True, exist_ok=True)
    for idx, img in enumerate(images, start=1):
        data: bytes = b""
        try:
            # openpyxl 的 Image._data 是可调用对象,返回 bytes
            if callable(getattr(img, "_data", None)):
                data = img._data()  # noqa: SLF001 - openpyxl 私有但稳定
            elif hasattr(img, "ref") and hasattr(img.ref, "getvalue"):
                data = img.ref.getvalue()
        except Exception as exc:  # noqa: BLE001
            print(
                f"  [WARN] sheet {ws.title} 第 {idx} 张图片读取失败: {exc}",
                file=sys.stderr,
            )
            continue

        if not data:
            continue

        # 锚点单元格(左上角)
        anchor_cell = "?"
        try:
            anchor = img.anchor
            if anchor is not None and getattr(anchor, "_from", None) is not None:
                # col/row 都是 0-based
                col_letter = get_column_letter(anchor._from.col + 1)
                row_num = anchor._from.row + 1
                anchor_cell = f"{col_letter}{row_num}"
        except Exception:
            pass

        ext = _sniff_ext(data)
        filename = f"{xlsx_stem}_{sheet_safe_name}_img{idx:02d}.{ext}"
        out_path = images_dir / filename
        try:
            out_path.write_bytes(data)
        except Exception as exc:  # noqa: BLE001
            print(f"  [WARN] 图片写入失败 {out_path}: {exc}", file=sys.stderr)
            continue

        results.append(
            {
                "filename": filename,
                "rel_path": f"images/{filename}",
                "anchor_cell": anchor_cell,
                "size_kb": round(len(data) / 1024, 1),
                "ext": ext,
                "width_emu": getattr(img, "width", None),
                "height_emu": getattr(img, "height", None),
            }
        )
    return results


def _safe_sheet_name(name: str) -> str:
    """把 sheet 名处理成文件名安全形式(中文、空格、特殊字符)。"""
    bad = '<>:"/\\|?*\n\r\t'
    out = "".join("_" if ch in bad else ch for ch in name).strip()
    return out[:50] or "sheet"


# ----------------------------------------------------------------------
# Sheet 检查器
# ----------------------------------------------------------------------


def inspect_sheet(
    ws: Worksheet,
    *,
    header_rows: int = 2,
    sample_rows: int = 5,
    max_cols_preview: int = 40,
    image_metas: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """把单个 sheet 转成 Markdown 片段。

    Args:
        image_metas: 如果外部已经抽好图片,传入 metadata 列表,会在 markdown 里引用
    """
    lines: List[str] = []
    name = ws.title
    max_row = ws.max_row or 0
    max_col = ws.max_column or 0

    lines.append(f"## Sheet: `{name}`")
    lines.append("")
    lines.append(f"- **总行数**: {max_row}")
    lines.append(f"- **总列数**: {max_col}")

    frozen = ws.freeze_panes
    if frozen:
        lines.append(f"- **冻结窗格**: `{frozen}`")

    # 合并单元格
    merged = list(ws.merged_cells.ranges) if ws.merged_cells else []
    if merged:
        lines.append(f"- **合并单元格** ({len(merged)} 组):")
        for mr in sorted(merged, key=lambda r: (r.min_row, r.min_col)):
            top_left = ws.cell(row=mr.min_row, column=mr.min_col).value
            lines.append(
                f"  - `{mr.coord}` → {_fmt_cell_value(top_left)}"
            )

    # 嵌入图片
    if image_metas:
        lines.append(f"- **嵌入图片** ({len(image_metas)} 张):")
        for meta in image_metas:
            lines.append(
                f"  - 锚点 `{meta['anchor_cell']}` · {meta['ext']} · {meta['size_kb']} KB · "
                f"[`{meta['filename']}`]({meta['rel_path']})"
            )
        lines.append("")
        lines.append("#### 图片预览")
        for meta in image_metas:
            lines.append("")
            lines.append(
                f"**锚点 {meta['anchor_cell']}** ({meta['ext']}, {meta['size_kb']} KB)"
            )
            lines.append("")
            lines.append(f"![{meta['filename']}]({meta['rel_path']})")

    # 数据验证
    dvs = getattr(ws, "data_validations", None)
    if dvs and dvs.dataValidation:
        lines.append(f"- **数据验证** ({len(dvs.dataValidation)} 条):")
        for dv in dvs.dataValidation:
            formula = dv.formula1 or ""
            lines.append(
                f"  - 范围 `{','.join(str(r) for r in dv.sqref.ranges) if dv.sqref else '?'}` "
                f"→ 类型={dv.type or 'list'} 值={_compact_formula(formula)}"
            )

    # 条件格式
    cf = ws.conditional_formatting
    cf_rules_count = 0
    if cf:
        for _rng, _rules in cf._cf_rules.items():  # noqa: SLF001 - openpyxl 的私有但稳定
            cf_rules_count += len(_rules)
    if cf_rules_count:
        lines.append(f"- **条件格式**: {cf_rules_count} 条规则(表头/互动/风险等常用于高亮)")

    # 批注
    comments: List[Tuple[str, str]] = []
    for row in ws.iter_rows(min_row=1, max_row=min(max_row, 200)):
        for cell in row:
            if cell.comment and cell.comment.text:
                comments.append((cell.coordinate, cell.comment.text.strip()))
    if comments:
        lines.append(f"- **单元格批注** ({len(comments)} 条,常含字段说明):")
        for coord, text in comments[:20]:
            shown = text.replace("\n", " ")[:120]
            lines.append(f"  - `{coord}`: {shown}")
        if len(comments) > 20:
            lines.append(f"  - ... (还有 {len(comments) - 20} 条未列出)")

    lines.append("")
    lines.append("### 表头(前 {} 行)".format(header_rows))
    lines.append("")

    if max_row == 0 or max_col == 0:
        lines.append("*(空 sheet)*")
        return "\n".join(lines)

    preview_cols = min(max_col, max_cols_preview)
    # 表头行 Markdown 表
    lines.append(
        "| 行 | "
        + " | ".join(f"**{get_column_letter(c)}**" for c in range(1, preview_cols + 1))
        + " |"
    )
    lines.append(
        "| --- | " + " | ".join("---" for _ in range(preview_cols)) + " |"
    )
    for r in range(1, min(header_rows, max_row) + 1):
        cells = [
            _fmt_cell_value(ws.cell(row=r, column=c).value)
            for c in range(1, preview_cols + 1)
        ]
        lines.append(f"| R{r} | " + " | ".join(cells) + " |")
    if max_col > preview_cols:
        lines.append(
            f"*(仅展示前 {preview_cols} 列,总共 {max_col} 列)*"
        )

    # 列类型推断 + 公式
    if max_row > header_rows:
        lines.append("")
        lines.append("### 列类型推断(基于 header 行后的前 {} 条数据)".format(sample_rows))
        lines.append("")
        lines.append("| 列 | 表头 | 推断类型 | 公式样例 | 非空率 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for c in range(1, preview_cols + 1):
            letter = get_column_letter(c)
            header = _fmt_cell_value(ws.cell(row=header_rows, column=c).value)
            col_type, formula_sample, non_empty_ratio = _infer_column(
                ws, col=c, start_row=header_rows + 1, sample=sample_rows
            )
            lines.append(
                f"| {letter} | {header} | {col_type} | {formula_sample} | {non_empty_ratio}% |"
            )

    # 数据样例
    if max_row > header_rows:
        lines.append("")
        lines.append("### 前 {} 行数据样例".format(sample_rows))
        lines.append("")
        lines.append(
            "| R | "
            + " | ".join(f"**{get_column_letter(c)}**" for c in range(1, preview_cols + 1))
            + " |"
        )
        lines.append(
            "| --- | " + " | ".join("---" for _ in range(preview_cols)) + " |"
        )
        for r in range(
            header_rows + 1, min(header_rows + 1 + sample_rows, max_row + 1)
        ):
            cells = [
                _fmt_cell_value(ws.cell(row=r, column=c).value)
                for c in range(1, preview_cols + 1)
            ]
            lines.append(f"| R{r} | " + " | ".join(cells) + " |")

    # 图表
    charts = getattr(ws, "_charts", [])
    if charts:
        lines.append("")
        lines.append(f"### 图表 ({len(charts)} 个)")
        for i, chart in enumerate(charts, 1):
            ct = type(chart).__name__
            title = ""
            try:
                if chart.title and hasattr(chart.title, "tx"):
                    title = str(chart.title.tx.rich.p[0].r[0].t) if chart.title.tx.rich else ""
            except Exception:
                pass
            lines.append(f"- [{i}] 类型={ct}" + (f" 标题={title!r}" if title else ""))

    return "\n".join(lines)


def _infer_column(
    ws: Worksheet, *, col: int, start_row: int, sample: int
) -> Tuple[str, str, int]:
    """根据前 sample 行推断列类型、公式样例、非空率(0-100)。"""
    types: List[str] = []
    formula_sample = "-"
    non_empty = 0
    total = 0
    for r in range(start_row, start_row + sample):
        if r > ws.max_row:
            break
        total += 1
        cell = ws.cell(row=r, column=col)
        v = cell.value
        if v is None or v == "":
            continue
        non_empty += 1
        if isinstance(v, str) and v.startswith("="):
            types.append("formula")
            if formula_sample == "-":
                formula_sample = f"`{_compact_formula(v)}`"
        elif cell.data_type == "f":  # formula type
            types.append("formula")
            if formula_sample == "-" and isinstance(v, str):
                formula_sample = f"`{_compact_formula(v)}`"
        elif isinstance(v, bool):
            types.append("bool")
        elif isinstance(v, (int, float)):
            # 判断百分比 / 整数 / 浮点
            fmt = (cell.number_format or "").lower()
            if "%" in fmt:
                types.append("percent")
            elif isinstance(v, int):
                types.append("int")
            else:
                types.append("float")
        elif isinstance(v, (datetime, date)):
            types.append("date")
        else:
            types.append("text")

    if not types:
        return ("*(无样例)*", formula_sample, 0)

    # 多数类型投票
    from collections import Counter

    top_type, _ = Counter(types).most_common(1)[0]
    non_empty_ratio = int(non_empty / total * 100) if total else 0
    return (top_type, formula_sample, non_empty_ratio)


def _fmt_cell_value(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        s = v.replace("\n", " ").replace("|", "\\|").strip()
        return s[:80] + ("…" if len(s) > 80 else "")
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".") or "0"
    return str(v)


def _compact_formula(s: str) -> str:
    s = s.replace("\n", " ")
    return s[:80] + ("…" if len(s) > 80 else "")


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------


def inspect_workbook(
    path: Path,
    *,
    header_rows: int,
    sample_rows: int,
    extract_images: bool = True,
    images_dir: Optional[Path] = None,
) -> str:
    wb = load_workbook(filename=str(path), data_only=False)
    sections: List[str] = []
    sections.append(f"# Excel 模板结构: `{path.name}`")
    sections.append("")
    sections.append(f"- 文件路径: `{path}`")
    sections.append(f"- Sheet 数: {len(wb.sheetnames)}")
    sections.append(f"- Sheet 列表: {', '.join(f'`{n}`' for n in wb.sheetnames)}")

    # 先把所有 sheet 的嵌入图片抽出来,再生成 markdown(方便统计总数)
    sheet_images: Dict[str, List[Dict[str, Any]]] = {}
    total_imgs = 0
    if extract_images and images_dir is not None:
        xlsx_stem = path.stem
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            metas = extract_sheet_images(
                ws,
                sheet_safe_name=_safe_sheet_name(sheet_name),
                xlsx_stem=xlsx_stem,
                images_dir=images_dir,
            )
            sheet_images[sheet_name] = metas
            total_imgs += len(metas)
        if total_imgs:
            sections.append(
                f"- 嵌入图片总数: **{total_imgs}** 张 (已抽取到 `{images_dir}`)"
            )
        else:
            sections.append("- 嵌入图片: 无")

    sections.append("")
    sections.append("---")
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        sections.append("")
        sections.append(
            inspect_sheet(
                ws,
                header_rows=header_rows,
                sample_rows=sample_rows,
                image_metas=sheet_images.get(sheet_name),
            )
        )
        sections.append("")
        sections.append("---")
    return "\n".join(sections)


def main() -> None:
    parser = argparse.ArgumentParser(description="Excel 模板结构抽取器")
    parser.add_argument(
        "target",
        help="xlsx 文件路径,或者一个包含 xlsx 的目录",
    )
    parser.add_argument(
        "--out",
        help="输出 Markdown 路径;不传时与 xlsx 同目录 + .md 后缀",
        default=None,
    )
    parser.add_argument(
        "--header-rows",
        type=int,
        default=2,
        help="把前 N 行视为表头(多级表头时设为 2 或 3),默认 2",
    )
    parser.add_argument(
        "--sample-rows",
        type=int,
        default=5,
        help="抽取的数据样本行数,默认 5",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="跳过嵌入图片抽取(默认开启,会把图片抽到 <输出目录>/images/)",
    )
    parser.add_argument(
        "--images-dir",
        default=None,
        help="图片输出目录;不传时与输出 Markdown 同目录下的 images/ 子目录",
    )
    args = parser.parse_args()

    target = Path(args.target)
    if not target.exists():
        print(f"[inspect_excel_template] 路径不存在: {target}", file=sys.stderr)
        sys.exit(1)

    files: List[Path]
    if target.is_dir():
        files = sorted(
            [
                p
                for p in target.glob("*.xlsx")
                if not p.name.startswith("~$")
            ]
        )
        if not files:
            print(f"[inspect_excel_template] {target} 下没有 xlsx 文件", file=sys.stderr)
            sys.exit(1)
    else:
        files = [target]

    for fp in files:
        print(f"[inspect_excel_template] 处理: {fp}")

        if args.out and len(files) == 1:
            out_path = Path(args.out)
        else:
            out_path = fp.with_suffix(".spec.md")
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if args.no_images:
            images_dir = None
        else:
            images_dir = (
                Path(args.images_dir)
                if args.images_dir
                else out_path.parent / "images"
            )

        try:
            md = inspect_workbook(
                fp,
                header_rows=args.header_rows,
                sample_rows=args.sample_rows,
                extract_images=not args.no_images,
                images_dir=images_dir,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  失败: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue

        out_path.write_text(md, encoding="utf-8")
        print(f"  已输出: {out_path}")
        if images_dir and images_dir.exists():
            img_count = len(list(images_dir.glob(f"{fp.stem}_*")))
            if img_count:
                print(f"  图片: {img_count} 张 → {images_dir}")


if __name__ == "__main__":
    main()
