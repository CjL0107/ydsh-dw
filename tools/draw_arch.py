# -*- coding: utf-8 -*-
"""生成第 2 周架构类配图：四层数仓架构图 + 星型模型图。"""
import os
from PIL import Image, ImageDraw, ImageFont

OUT = r"C:\Users\Lenovo\Desktop\ydsh-dw\docs\screenshots"
os.makedirs(OUT, exist_ok=True)

FONT = r"C:\Windows\Fonts\msyh.ttc"
FONT_B = r"C:\Windows\Fonts\msyhbd.ttc"


def f(size, bold=False):
    return ImageFont.truetype(FONT_B if bold else FONT, size)


def rrect(d, box, r, fill, outline, width=2):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


def ctext(d, box, text, font, fill, dy=0):
    x0, y0, x1, y1 = box
    w = d.textlength(text, font=font)
    d.text(((x0 + x1 - w) / 2, (y0 + y1) / 2 - font.size * 0.62 + dy), text, font=font, fill=fill)


def ctext_multi(d, box, lines, font, fill, gap=6):
    x0, y0, x1, y1 = box
    lh = font.size + gap
    total = lh * len(lines) - gap
    cy = (y0 + y1) / 2 - total / 2
    for line in lines:
        w = d.textlength(line, font=font)
        d.text(((x0 + x1 - w) / 2, cy), line, font=font, fill=fill)
        cy += lh


def arrow(d, x, y0, y1, color, w=3):
    d.line([(x, y0), (x, y1 - 12)], fill=color, width=w)
    d.polygon([(x - 9, y1 - 14), (x + 9, y1 - 14), (x, y1)], fill=color)


# ============================================================
# 图 1：四层数仓架构图
# ============================================================
W, H = 1800, 1320
img = Image.new("RGB", (W, H), "#FFFFFF")
d = ImageDraw.Draw(img)

INK = "#1F2937"
INK2 = "#475569"
GRID = "#CBD5E1"

d.text((60, 42), "云购生活 · 企业级数据仓库四层架构", font=f(40, True), fill=INK)
d.text((60, 96), "ODS 贴源层  →  DWD 明细层  →  DWS 汇总层  →  ADS 应用层     |     星型模型 · 分区/分桶 · 编码映射",
       font=f(21), fill=INK2)

LAYERS = [
    dict(key="SRC", y0=170, y1=300, name="数据源", sub="业务系统 6 张表",
         fill="#EDF1F7", line="#8296B4", items=[
             "ods_user.csv 1,000", "ods_product.csv 600", "ods_store.csv 60",
             "ods_order.csv 15,600", "ods_order_item.csv 37,549", "ods_user_behavior.csv 60,000"]),
    dict(key="ODS", y0=350, y1=500, name="ODS 贴源层", sub="ydsh_ods · ods_ 前缀\n原样接入，不做清洗",
         fill="#E7F3EC", line="#6F9E82", items=[
             "ods_user", "ods_product", "ods_store",
             "ods_order", "ods_order_item", "ods_user_behavior"]),
    dict(key="DWD", y0=550, y1=830, name="DWD 明细层", sub="ydsh_dwd · dim_ / dwd_ 前缀\n清洗 + 标准化 + 维度建模",
         fill="#E8EEF9", line="#4A6FA5", items=[]),
    dict(key="DWS", y0=880, y1=1010, name="DWS 汇总层", sub="ydsh_dws · dws_ 前缀\n按主题轻度汇总（第 3 周）",
         fill="#FBF1E4", line="#BE8A52", items=[
             "dws_sales_day", "dws_user_day", "dws_product_day", "dws_store_day"]),
    dict(key="ADS", y0=1060, y1=1190, name="ADS 应用层", sub="ydsh_ads · ads_ 前缀\n指标结果（第 3 周）",
         fill="#F9E9E9", line="#B36A6A", items=[
             "ads_kpi_overview", "ads_category_rank", "ads_province_rank", "ads_user_rfm"]),
]

LABEL_X0, LABEL_X1 = 60, 300
MAIN_X0, MAIN_X1 = 320, 1490

for L in LAYERS:
    # 左侧层名标签
    rrect(d, (LABEL_X0, L["y0"], LABEL_X1, L["y1"]), 12, L["fill"], L["line"], 3)
    name_font = f(28, True)
    sub_font = f(17)
    lines = L["sub"].split("\n")
    block_h = 38 + len(lines) * 24
    top = (L["y0"] + L["y1"]) / 2 - block_h / 2
    w = d.textlength(L["name"], font=name_font)
    d.text(((LABEL_X0 + LABEL_X1 - w) / 2, top), L["name"], font=name_font, fill=L["line"])
    yy = top + 40
    for ln in lines:
        w2 = d.textlength(ln, font=sub_font)
        d.text(((LABEL_X0 + LABEL_X1 - w2) / 2, yy), ln, font=sub_font, fill=INK2)
        yy += 24

    # 右侧主区
    rrect(d, (MAIN_X0, L["y0"], MAIN_X1, L["y1"]), 14, L["fill"], L["line"], 3)

# 数据源：6 个竖排小卡片
y = 190
for i, it in enumerate(LAYERS[0]["items"]):
    col = i % 3
    row = i // 3
    x0 = MAIN_X0 + 24 + col * 388
    y0 = 192 + row * 52
    rrect(d, (x0, y0, x0 + 366, y0 + 40), 8, "#FFFFFF", LAYERS[0]["line"], 2)
    d.text((x0 + 16, y0 + 8), it, font=f(18), fill=INK)

# ODS：6 个卡片
for i, it in enumerate(LAYERS[1]["items"]):
    col = i % 3
    row = i // 3
    x0 = MAIN_X0 + 24 + col * 388
    y0 = 372 + row * 60
    rrect(d, (x0, y0, x0 + 366, y0 + 46), 8, "#FFFFFF", LAYERS[1]["line"], 2)
    d.text((x0 + 44, y0 + 10), it, font=f(19), fill=INK)

# DWD 内部：星型模型
d.text((MAIN_X0 + 24, 566), "星型模型：1 张订单事实表 + 4 张维度表 + 3 张明细/行为事实表",
       font=f(19, True), fill=LAYERS[2]["line"])

fact = (MAIN_X0 + 400, 620, MAIN_X0 + 780, 700)
rrect(d, fact, 10, "#DCE6F6", LAYERS[2]["line"], 3)
ctext_multi(d, fact, ["dwd_order  订单事实表", "按 order_date 分 366 区"], f(19, True), "#24405F", 4)

dims = [
    ((MAIN_X0 + 60, 610, MAIN_X0 + 340, 680), "dim_user  用户维度"),
    ((MAIN_X0 + 60, 720, MAIN_X0 + 340, 790), "dim_product  商品维度"),
    ((MAIN_X0 + 840, 610, MAIN_X0 + 1140, 680), "dim_store  门店维度"),
    ((MAIN_X0 + 840, 720, MAIN_X0 + 1140, 790), "dim_date  时间维度"),
]
for box, txt in dims:
    rrect(d, box, 10, "#F4F7FD", LAYERS[2]["line"], 2)
    ctext(d, box, txt, f(18), INK)

for box, _ in dims:
    x0, y0, x1, y1 = box
    if x1 < fact[0]:
        d.line([(x1, (y0 + y1) // 2), (fact[0], (fact[1] + fact[3]) // 2)], fill=LAYERS[2]["line"], width=2)
    else:
        d.line([(fact[2], (fact[1] + fact[3]) // 2), (x0, (y0 + y1) // 2)], fill=LAYERS[2]["line"], width=2)

extra = [
    ((690, 736, 1110, 800), ["dwd_order_item / dwd_user_behavior", "明细事实 · 分区对齐订单日期"]),
]
for box, lines in extra:
    rrect(d, box, 10, "#F4F7FD", LAYERS[2]["line"], 2)
    ctext_multi(d, box, lines, f(15), INK, 4)
d.line([(905, 700), (905, 736)], fill=LAYERS[2]["line"], width=2)

# DWS / ADS 卡片
for L, yy in ((LAYERS[3], 900), (LAYERS[4], 1080)):
    for i, it in enumerate(L["items"]):
        x0 = MAIN_X0 + 24 + i * 292
        rrect(d, (x0, yy, x0 + 270, yy + 44), 8, "#FFFFFF", L["line"], 2)
        ctext(d, (x0, yy, x0 + 270, yy + 44), it, f(17), INK)

# 层间箭头
for x in (MAIN_X0 + 120, MAIN_X0 + 585, MAIN_X0 + 1050):
    arrow(d, x, 300, 350, GRID)
    arrow(d, x, 500, 550, GRID)
    arrow(d, x, 830, 880, GRID)
    arrow(d, x, 1010, 1060, GRID)

# 右侧数据治理竖条
gx0, gx1 = 1520, 1740
rrect(d, (gx0, 170, gx1, 1190), 14, "#F7F7F8", "#A8A29E", 3)
ctext_multi(d, (gx0, 180, gx1, 232), ["数据", "治理"], f(22, True), "#57534E", 2)
gov = ["Git 版本控制", "质量校验", "枚举字典", "元数据", "分区管理", "调度依赖"]
yy = 260
for g in gov:
    rrect(d, (gx0 + 18, yy, gx1 - 18, yy + 46), 8, "#FFFFFF", "#C7C2BC", 2)
    ctext(d, (gx0 + 18, yy, gx1 - 18, yy + 46), g, f(16), INK)
    yy += 62

d.text((60, 1225), "说明：DWS / ADS 两层为第 3 周建设内容，本周只完成架构设计与依赖规划；表名在图中为规划名。",
       font=f(18), fill=INK2)

img.save(os.path.join(OUT, "arch_4layer.png"))

# ============================================================
# 图 2：星型模型图
# ============================================================
W2, H2 = 1600, 1000
img2 = Image.new("RGB", (W2, H2), "#FFFFFF")
d = ImageDraw.Draw(img2)
d.text((56, 40), "DWD 层星型模型（Star Schema）", font=f(36, True), fill=INK)
d.text((56, 92), "事实表存度量与外键，维度表存描述属性；事实表按 order_date 分区，维度表 SCD1 全量覆盖",
       font=f(19), fill=INK2)

BLUE, BLUE_F = "#4A6FA5", "#E8EEF9"
GREEN, GREEN_F = "#6F9E82", "#E7F3EC"
ORANGE, ORANGE_F = "#BE8A52", "#FBF1E4"

center = (640, 420, 960, 540)
rrect(d, center, 12, "#DCE6F6", BLUE, 3)
ctext_multi(d, center, ["dwd_order", "订单事实表（粒度：1 订单 = 1 行）"], f(20, True), "#24405F", 5)

# 事实表度量
meas = (596, 548, 1004, 646)
rrect(d, meas, 10, "#FFFFFF", BLUE, 2)
ctext_multi(d, meas, ["度量：pay_amount / discount_amount",
                      "外键：user_id · store_id · order_date",
                      "编码键：pay_code / status_code / channel_code"], f(14), INK2, 4)

dims2 = [
    ((80, 300, 460, 460), "dim_user  用户维度", ["user_id 主键", "gender / age", "province / city / region", "member_level / level_code"]),
    ((80, 620, 460, 790), "dim_product  商品维度", ["product_id 主键", "category / sub_category", "brand / price / cost", "status / launch_date"]),
    ((1140, 300, 1520, 460), "dim_store  门店维度", ["store_id 主键", "province / city", "region / store_level", "area / manager"]),
    ((1140, 620, 1520, 790), "dim_date  时间维度", ["date_key 主键", "year / quarter / month", "week_of_year / weekday", "is_weekend / is_promo_day"]),
]
for box, title, attrs in dims2:
    rrect(d, box, 12, GREEN_F, GREEN, 3)
    x0, y0, x1, y1 = box
    d.text((x0 + 18, y0 + 14), title, font=f(19, True), fill="#3C6B52")
    yy = y0 + 52
    for a in attrs:
        d.text((x0 + 22, yy), "· " + a, font=f(16), fill=INK)
        yy += 26

# 连接线
pairs = [
    ((460, 380), (640, 440)),
    ((460, 705), (640, 520)),
    ((1140, 380), (960, 440)),
    ((1140, 705), (960, 520)),
]
for (x0, y0), (x1, y1) in pairs:
    d.line([(x0, y0), (x1, y1)], fill="#94A3B8", width=3)
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    d.text((mx - 14, my - 26), "1:N", font=f(15, True), fill="#64748B")

# 明细事实
sub = (560, 790, 1040, 880)
rrect(d, sub, 12, ORANGE_F, ORANGE, 3)
ctext_multi(d, sub, ["dwd_order_item  明细事实", "按 order_date 分区 · 粒度：1 明细 = 1 行"], f(18, True), "#7A5227", 4)
d.line([(800, 640), (800, 790)], fill="#94A3B8", width=3)

d.text((56, 930), "编码映射维度表：dim_code_member_level / dim_code_pay_type / dim_code_order_status / dim_code_channel / dim_code_behavior",
       font=f(16), fill=INK2)

img2.save(os.path.join(OUT, "star_schema.png"))
print("saved", os.path.join(OUT, "arch_4layer.png"), os.path.join(OUT, "star_schema.png"))
