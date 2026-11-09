# -*- coding: utf-8 -*-
"""轻量 Markdown -> DOCX 转换器（第 2 周文档交付用）。

支持：# ~ #### 标题、段落、- / 1. 列表、> 引用、``` 代码块、| 表格 |、![图](路径)、
行内 **加粗** 与 `代码`、--- 分隔线。附带封面、目录域、页脚页码。

用法：
    python tools/md2docx.py <md文件> <docx输出> "<封面主标题>" "<封面副标题>"
"""
import os
import re
import sys

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

INK = RGBColor(0x1F, 0x29, 0x37)
GRAY = RGBColor(0x5B, 0x66, 0x77)
ACCENT = RGBColor(0x1F, 0x38, 0x64)
HEAD_BG = "1F3864"
ZEBRA = "F2F5FA"
CODE_BG = "F5F5F5"


def set_cell_bg(cell, color):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color)
    tcPr.append(shd)


def set_para_bg(par, color):
    pPr = par._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color)
    pPr.append(shd)


def set_run_font(run, name="宋体", size=10.5, bold=False, color=INK, ascii_font="Times New Roman"):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = ascii_font
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.append(rFonts)
    rFonts.set(qn("w:eastAsia"), name)
    rFonts.set(qn("w:ascii"), ascii_font)
    rFonts.set(qn("w:hAnsi"), ascii_font)


def add_page_number_footer(section):
    footer = section.footer
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    set_run_font(run, size=9, color=GRAY)
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "1"
    r.append(t)
    fld.append(r)
    p._p.append(fld)


def add_toc(doc):
    p = doc.add_paragraph()
    run = p.add_run()
    fldChar = OxmlElement("w:fldChar")
    fldChar.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = r'TOC \o "1-3" \h \z \u'
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    placeholder = OxmlElement("w:t")
    placeholder.text = "（在 Word 中右键此处 → 更新域，生成目录）"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.append(fldChar)
    run._r.append(instr)
    run._r.append(sep)
    run._r.append(placeholder)
    run._r.append(end)
    set_run_font(run, size=10, color=GRAY)


def add_inline(par, text, size=10.5, base_bold=False, color=INK, name="宋体"):
    """处理 **加粗** 与 `等宽` 两种行内标记。"""
    tokens = re.split(r"(\*\*.+?\*\*|`[^`]+`)", text)
    for tok in tokens:
        if not tok:
            continue
        if tok.startswith("**") and tok.endswith("**") and len(tok) > 4:
            r = par.add_run(tok[2:-2])
            set_run_font(r, name=name, size=size, bold=True, color=color)
        elif tok.startswith("`") and tok.endswith("`") and len(tok) > 2:
            r = par.add_run(tok[1:-1])
            set_run_font(r, name="宋体", size=size - 0.5, bold=base_bold,
                         color=RGBColor(0xA3, 0x2B, 0x2B), ascii_font="Consolas")
        else:
            r = par.add_run(tok)
            set_run_font(r, name=name, size=size, bold=base_bold, color=color)


def body_para(doc, space_after=6, first_line_indent=0):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(space_after)
    pf.line_spacing = 1.45
    if first_line_indent:
        pf.first_line_indent = Cm(first_line_indent)
    return p


def add_heading(doc, level, text):
    sizes = {1: 16, 2: 14, 3: 12, 4: 11}
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(14 if level <= 2 else 10)
    pf.space_after = Pt(8 if level <= 2 else 6)
    pf.keep_with_next = True
    r = p.add_run(text)
    set_run_font(r, name="黑体", size=sizes.get(level, 11), bold=True,
                 color=ACCENT if level <= 2 else INK)
    # 让 Word 导航窗格能识别为标题
    p.style = doc.styles["Heading %d" % min(level, 4)]
    for run in p.runs:
        set_run_font(run, name="黑体", size=sizes.get(level, 11), bold=True,
                     color=ACCENT if level <= 2 else INK)
    return p


def add_table(doc, rows):
    ncol = len(rows[0])
    t = doc.add_table(rows=len(rows), cols=ncol)
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = True
    for i, row in enumerate(rows):
        for j in range(ncol):
            cell = t.cell(i, j)
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.line_spacing = 1.1
            txt = row[j] if j < len(row) else ""
            if i == 0:
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                add_inline(p, txt, size=9, base_bold=True, color=RGBColor(0xFF, 0xFF, 0xFF), name="黑体")
                set_cell_bg(cell, HEAD_BG)
            else:
                align = WD_ALIGN_PARAGRAPH.LEFT
                if re.fullmatch(r"[-+0-9.,%]+", txt.strip() or "x"):
                    align = WD_ALIGN_PARAGRAPH.RIGHT
                p.alignment = align
                add_inline(p, txt, size=9)
                if i % 2 == 0:
                    set_cell_bg(cell, ZEBRA)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)


def add_code_block(doc, lines):
    for ln in lines:
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.space_before = Pt(0)
        pf.space_after = Pt(0)
        pf.line_spacing = 1.15
        pf.left_indent = Cm(0.4)
        set_para_bg(p, CODE_BG)
        r = p.add_run(ln if ln else " ")
        set_run_font(r, name="宋体", size=8.5, color=RGBColor(0x33, 0x33, 0x33), ascii_font="Consolas")
    doc.add_paragraph().paragraph_format.space_after = Pt(4)


def add_image(doc, path, alt):
    if not os.path.isabs(path):
        path = os.path.abspath(path)
    if not os.path.exists(path):
        p = body_para(doc)
        add_inline(p, "[图片缺失: %s]" % alt, size=9, color=GRAY)
        return
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(4)
    p.add_run().add_picture(path, width=Cm(15.2))

    if alt:
        cap = doc.add_paragraph()
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        cap.paragraph_format.space_before = Pt(2)
        cap.paragraph_format.space_after = Pt(10)
        cr = cap.add_run(alt)
        set_run_font(cr, size=9, color=GRAY)


def add_quote(doc, lines):
    for ln in lines:
        p = body_para(doc, space_after=2)
        p.paragraph_format.left_indent = Cm(0.6)
        r = p.add_run("│ ")
        set_run_font(r, size=10, color=ACCENT, bold=True)
        add_inline(p, ln, size=9.5, color=GRAY)


def add_list(doc, text, ordered, level=0):
    p = body_para(doc, space_after=4)
    p.paragraph_format.left_indent = Cm(0.75 + 0.6 * level)
    p.paragraph_format.first_line_indent = Cm(-0.45)
    marker = "· " if not ordered else ""
    r = p.add_run(marker)
    set_run_font(r, size=10.5, color=ACCENT, bold=True)
    add_inline(p, text, size=10.5)


def build_cover(doc, title, subtitle, meta_lines):
    for _ in range(4):
        doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(title)
    set_run_font(r, name="黑体", size=26, bold=True, color=ACCENT)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(10)
    r = p.add_run(subtitle)
    set_run_font(r, name="黑体", size=14, color=GRAY)

    for _ in range(6):
        doc.add_paragraph()

    for line in meta_lines:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(6)
        r = p.add_run(line)
        set_run_font(r, size=11, color=INK)

    doc.add_page_break()


def convert(md_path, docx_path, title, subtitle, meta_lines):
    doc = Document()
    sec = doc.sections[0]
    sec.page_width = Cm(21)
    sec.page_height = Cm(29.7)
    sec.left_margin = Cm(2.6)
    sec.right_margin = Cm(2.6)
    sec.top_margin = Cm(2.4)
    sec.bottom_margin = Cm(2.2)
    add_page_number_footer(sec)

    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(10.5)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    build_cover(doc, title, subtitle, meta_lines)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("目　录")
    set_run_font(r, name="黑体", size=16, bold=True, color=ACCENT)
    add_toc(doc)
    doc.add_page_break()

    base_dir = os.path.dirname(os.path.abspath(md_path))
    with open(md_path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    i = 0
    in_code = False
    code_buf = []
    table_buf = []
    quote_buf = []
    para_buf = []

    def flush_para():
        if para_buf:
            text = " ".join(para_buf).strip()
            if text:
                p = body_para(doc, first_line_indent=0.74)
                add_inline(p, text)
            para_buf.clear()

    def flush_table():
        if table_buf:
            rows = []
            for ln in table_buf:
                cells = [c.strip() for c in ln.strip().strip("|").split("|")]
                if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    continue
                rows.append(cells)
            if rows:
                add_table(doc, rows)
            table_buf.clear()

    def flush_quote():
        if quote_buf:
            add_quote(doc, quote_buf)
            quote_buf.clear()

    for raw in lines:
        line = raw.rstrip()

        if in_code:
            if line.strip().startswith("```"):
                add_code_block(doc, code_buf)
                code_buf = []
                in_code = False
            else:
                code_buf.append(line)
            continue

        if line.strip().startswith("```"):
            flush_para(); flush_table(); flush_quote()
            in_code = True
            continue

        if line.strip().startswith("|"):
            flush_para(); flush_quote()
            table_buf.append(line)
            continue
        else:
            flush_table()

        if line.startswith(">"):
            flush_para()
            quote_buf.append(line.lstrip(">").strip())
            continue
        else:
            flush_quote()

        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            flush_para()
            level = len(m.group(1))
            text = m.group(2).strip()
            if level == 1:
                # 文档内的一级标题已由封面承担，这里降为节标题
                level = 2
            add_heading(doc, level, text)
            continue

        if re.fullmatch(r"\s*(-{3,}|\*{3,})\s*", line):
            flush_para()
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(6)
            pPr = p._p.get_or_add_pPr()
            pbdr = OxmlElement("w:pBdr")
            bottom = OxmlElement("w:bottom")
            bottom.set(qn("w:val"), "single")
            bottom.set(qn("w:sz"), "6")
            bottom.set(qn("w:color"), "D0D7E2")
            pbdr.append(bottom)
            pPr.append(pbdr)
            continue

        m = re.match(r"^!\[(.*?)\]\((.*?)\)\s*$", line)
        if m:
            flush_para()
            rel = m.group(2)
            p_join = os.path.join(base_dir, rel)
            # md 在 docs/ 下、图片写的是仓库根相对路径时，退回仓库根再找一次
            if not os.path.exists(p_join):
                p_root = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), rel
                )
                if os.path.exists(p_root):
                    p_join = p_root
            add_image(doc, p_join, m.group(1))
            continue

        m = re.match(r"^(\s*)[-*]\s+(.*)$", line)
        if m:
            flush_para()
            add_list(doc, m.group(2).strip(), False, level=len(m.group(1)) // 2)
            continue

        m = re.match(r"^(\s*)(\d+)\.\s+(.*)$", line)
        if m:
            flush_para()
            p = body_para(doc, space_after=4)
            p.paragraph_format.left_indent = Cm(0.75 + 0.6 * (len(m.group(1)) // 2))
            p.paragraph_format.first_line_indent = Cm(-0.45)
            r = p.add_run("%s. " % m.group(2))
            set_run_font(r, size=10.5, color=ACCENT, bold=True)
            add_inline(p, m.group(3).strip(), size=10.5)
            continue

        if not line.strip():
            flush_para()
            continue

        para_buf.append(line.strip())

    flush_para(); flush_table(); flush_quote()
    if code_buf:
        add_code_block(doc, code_buf)

    doc.save(docx_path)
    return docx_path


if __name__ == "__main__":
    md, out, title, subtitle = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    meta = sys.argv[5:] if len(sys.argv) > 5 else []
    if not meta:
        meta = ["云购生活 · 企业级数据仓库与经营分析可视化系统",
                "企业项目实战实训　|　数据科学与大数据技术",
                "梁常金　|　2026-11-02"]
    print(convert(md, out, title, subtitle, meta))
