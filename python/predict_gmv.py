#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
第 3 周 · 子任务 2：时间序列分析与预测建模
============================================================
输入：dws_sales_day 导出的每日 GMV 序列（.tmp/w3/daily_gmv.csv）
输出：趋势分解图、模型评估图、未来 7 天预测、指标 JSON

流程：
  1. 描述性分析：趋势（7/30 日移动平均）、周内季节性、月度分布
  2. 同比/环比：本数据集只有 2024 一年，无法做跨年同比，
     退化为「周同比（本周同星期 vs 上周同星期）」与「月环比」
  3. 特征工程：滞后项 + 滚动统计 + 日历特征 + 大促标记
  4. 建模：线性回归（基线） + 梯度提升树（XGBoost，若不可用退回 sklearn GBDT）
  5. 评估：时间序列切分（前 330 天训练 / 后 36 天测试），MAE / RMSE / MAPE
  6. 预测：未来 7 天，递归单步预测（每步用上一预测值填滞后项）

注意：本数据集为合成数据，分布被有意做均匀，模型精度高不代表业务可预测性高，
      这一点在《预测建模分析报告》的"适用边界"一节单独说明。
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

from sklearn.linear_model import LinearRegression
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

# ---------------------------------------------------------------- 环境
ROOT = Path(__file__).resolve().parent.parent
IN_CSV = ROOT / ".tmp" / "w3" / "daily_gmv.csv"
OUT_DIR = ROOT / "python" / "outputs"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 中文字体（Windows 自带雅黑）
for fp in [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"]:
    if os.path.exists(fp):
        font_manager.fontManager.addfont(fp)
        plt.rcParams["font.sans-serif"] = [font_manager.FontProperties(fname=fp).get_name()]
        break
plt.rcParams["axes.unicode_minus"] = False

# 已知大促日期（数据集为 2024 年）
PROMO_DATES = {"2024-06-18", "2024-11-11", "2024-12-12", "2024-01-01"}

TRAIN_DAYS = 330   # 训练集长度
FORECAST_H = 7     # 预测未来 7 天


def load_series() -> pd.DataFrame:
    df = pd.read_csv(IN_CSV, encoding="utf-8-sig")
    df.columns = [c.strip().lstrip("\ufeff") for c in df.columns]
    df["stat_date"] = pd.to_datetime(df["stat_date"])
    df = df.sort_values("stat_date").set_index("stat_date")
    for c in ["paid_order_cnt", "gmv", "aov", "item_qty"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def descriptive(df: pd.DataFrame) -> dict:
    """描述性统计：趋势、周内季节性、月度分布、周同比"""
    s = df["gmv"]
    out = {
        "days": int(len(s)),
        "date_min": str(s.index.min().date()),
        "date_max": str(s.index.max().date()),
        "gmv_total": round(float(s.sum()), 2),
        "gmv_mean": round(float(s.mean()), 2),
        "gmv_std": round(float(s.std()), 2),
        "gmv_cv": round(float(s.std() / s.mean()), 4),   # 变异系数
        "gmv_min": round(float(s.min()), 2),
        "gmv_max": round(float(s.max()), 2),
    }

    # 周内季节性：按星期几求均值
    wd = s.groupby(s.index.day_of_week).mean()
    names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    out["weekday_mean"] = {names[i]: round(float(v), 2) for i, v in wd.items()}
    out["weekday_amplitude"] = round(float(wd.max() - wd.min()), 2)
    out["weekday_amplitude_pct"] = round(float((wd.max() - wd.min()) / s.mean() * 100), 2)

    # 月度
    m = s.groupby(s.index.to_period("M")).sum()
    out["month_gmv"] = {str(k): round(float(v), 2) for k, v in m.items()}
    mom = m.pct_change().dropna()
    out["month_mom_mean"] = round(float(mom.mean()), 4)
    out["month_mom_std"] = round(float(mom.std()), 4)

    # 周同比（本周 vs 上周同星期）：本质是 lag7 的相对变化
    wow = s.pct_change(7).dropna()
    out["wow_mean"] = round(float(wow.mean()), 4)
    out["wow_std"] = round(float(wow.std()), 4)
    out["wow_abs_mean"] = round(float(wow.abs().mean()), 4)

    # 移动平均对比：7 日 vs 30 日
    ma7, ma30 = s.rolling(7).mean(), s.rolling(30).mean()
    out["ma7_last"] = round(float(ma7.iloc[-1]), 2)
    out["ma30_last"] = round(float(ma30.iloc[-1]), 2)
    out["ma7_below_ma30_days"] = int((ma7 < ma30).sum())
    return out


def decompose(df: pd.DataFrame):
    """简易分解：趋势 = 中心化 7 日移动平均；周季节 = 去趋势后按星期几的均值；残差 = 剩余"""
    s = df["gmv"].astype(float)
    trend = s.rolling(7, center=True).mean()
    detrended = s - trend
    seasonal_wd = detrended.groupby(detrended.index.day_of_week).mean()
    seasonal = pd.Series(detrended.index.day_of_week.map(seasonal_wd), index=detrended.index)
    resid = s - trend - seasonal
    return trend, seasonal, resid


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    s = df["gmv"].astype(float)
    f = pd.DataFrame(index=s.index)
    f["y"] = s
    f["lag1"] = s.shift(1)
    f["lag2"] = s.shift(2)
    f["lag7"] = s.shift(7)
    f["lag14"] = s.shift(14)
    f["ma7"] = s.shift(1).rolling(7).mean()
    f["ma14"] = s.shift(1).rolling(14).mean()
    f["std7"] = s.shift(1).rolling(7).std()
    f["weekday"] = s.index.day_of_week
    f["is_weekend"] = (s.index.day_of_week >= 5).astype(int)
    f["month"] = s.index.month
    f["day"] = s.index.day
    f["is_promo"] = s.index.strftime("%Y-%m-%d").isin(PROMO_DATES).astype(int)
    return f.dropna()


def make_models():
    """优先用 XGBoost；未安装则退回 sklearn 梯度提升（同属树模型，结论一致）"""
    models = {"LinearRegression": LinearRegression()}
    try:
        from xgboost import XGBRegressor
        models["XGBoost"] = XGBRegressor(
            n_estimators=400, max_depth=3, learning_rate=0.05,
            subsample=0.9, colsample_bytree=0.9, random_state=42,
            objective="reg:squarederror",
        )
        used = "XGBoost"
    except Exception:
        models["GradientBoosting"] = GradientBoostingRegressor(
            n_estimators=400, max_depth=3, learning_rate=0.05,
            subsample=0.9, random_state=42,
        )
        used = "GradientBoosting(sklearn)"
    return models, used


def evaluate(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    mae = mean_absolute_error(y_true, y_pred)
    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    mape = float(np.mean(np.abs((y_true - y_pred) / y_true)) * 100)
    # 简单基准：用 lag1（昨天）当预测
    return {"MAE": round(float(mae), 2), "RMSE": round(rmse, 2), "MAPE": round(mape, 2)}


def recursive_forecast(model, feat: pd.DataFrame, history: pd.Series, cols, h: int):
    """递归预测：每步用上一步预测值回填滞后项与滚动统计"""
    hist = history.copy()
    preds, dates = [], []
    last_date = hist.index.max()
    for i in range(1, h + 1):
        d = last_date + pd.Timedelta(days=i)
        row = {}
        row["lag1"] = hist.iloc[-1]
        row["lag2"] = hist.iloc[-2]
        row["lag7"] = hist.iloc[-7]
        row["lag14"] = hist.iloc[-14]
        row["ma7"] = hist.iloc[-7:].mean()
        row["ma14"] = hist.iloc[-14:].mean()
        row["std7"] = hist.iloc[-7:].std()
        row["weekday"] = d.day_of_week
        row["is_weekend"] = int(d.day_of_week >= 5)
        row["month"] = d.month
        row["day"] = d.day
        row["is_promo"] = int(d.strftime("%Y-%m-%d") in PROMO_DATES)
        x = pd.DataFrame([row])[cols]
        yhat = float(model.predict(x)[0])
        yhat = max(yhat, 0.0)
        preds.append(yhat)
        dates.append(d)
        hist.loc[d] = yhat
    return pd.Series(preds, index=pd.DatetimeIndex(dates))


def main():
    df = load_series()
    print(f"读入 {len(df)} 天数据：{df.index.min().date()} ~ {df.index.max().date()}")

    stats = descriptive(df)

    # ---------------- 建模 ----------------
    feat = build_features(df)
    cols = [c for c in feat.columns if c != "y"]

    train = feat.iloc[:TRAIN_DAYS]
    test = feat.iloc[TRAIN_DAYS:]
    Xtr, ytr = train[cols], train["y"]
    Xte, yte = test[cols], test["y"]

    models, used_tree = make_models()
    results, preds_store = {}, {}
    for name, mdl in models.items():
        mdl.fit(Xtr, ytr)
        p_in = mdl.predict(Xtr)
        p_out = mdl.predict(Xte)
        results[name] = {
            "train": evaluate(ytr.values, p_in),
            "test": evaluate(yte.values, p_out),
        }
        preds_store[name] = p_out

    # 朴素基线：直接用 lag1 预测
    baseline = evaluate(yte.values, test["lag1"].values)

    # 选测试集 MAPE 最好的模型做最终预测
    best_name = min(results, key=lambda k: results[k]["test"]["MAPE"])
    best_model = models[best_name]

    # 特征重要度：取树模型（解释力来源与"最优模型"无关，
    # 线性回归没有 feature_importances_，不能拿 best_model 去取）
    importances = {}
    for cand in (models.get(used_tree), best_model):
        if cand is not None and hasattr(cand, "feature_importances_"):
            imp = pd.Series(cand.feature_importances_, index=cols).sort_values(ascending=False)
            importances = {k: round(float(v), 4) for k, v in imp.head(8).items()}
            break

    # ---------------- 未来 7 天 ----------------
    fut = recursive_forecast(best_model, feat, df["gmv"], cols, FORECAST_H)

    # ---------------- 出图 ----------------
    trend, seasonal, resid = decompose(df)

    # 图 1：原始序列 + 7/30 日移动平均 + 未来预测
    fig, ax = plt.subplots(figsize=(11, 4.2), dpi=150)
    ax.plot(df.index, df["gmv"], lw=0.8, color="#9AA5B1", label="日 GMV")
    ax.plot(df.index, trend, lw=1.8, color="#1F3864", label="7 日移动平均（趋势）")
    ax.plot(df.index, df["gmv"].rolling(30).mean(), lw=1.4, color="#C00000", ls="--", label="30 日移动平均")
    ax.plot(fut.index, fut.values, marker="o", ms=4, lw=1.6, color="#ED7D31", label="未来 7 天预测")
    ax.axvline(df.index.max(), color="#7F7F7F", ls=":", lw=1)
    ax.set_title("云购生活 2024 年每日 GMV 与未来 7 天预测", fontsize=12, color="#1F3864")
    ax.set_ylabel("GMV（元）")
    ax.legend(fontsize=8, ncol=4, loc="upper left")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "fig_timeseries.png")
    plt.close(fig)

    # 图 2：趋势 / 周季节 / 残差
    fig, axes = plt.subplots(3, 1, figsize=(11, 6.2), dpi=150, sharex=True)
    axes[0].plot(df.index, trend, color="#1F3864", lw=1.4)
    axes[0].set_ylabel("趋势")
    axes[1].plot(df.index, seasonal, color="#2E75B6", lw=1.0)
    axes[1].set_ylabel("周季节")
    axes[2].plot(df.index, resid, color="#7F7F7F", lw=0.7)
    axes[2].axhline(0, color="#C00000", lw=0.8)
    axes[2].set_ylabel("残差")
    for a in axes:
        a.grid(alpha=0.2)
    axes[0].set_title("GMV 序列分解：趋势 + 周内季节性 + 残差", fontsize=12, color="#1F3864")
    fig.tight_layout()
    fig.savefig(OUT_DIR / "fig_decompose.png")
    plt.close(fig)

    # 图 3：测试集真实 vs 预测（两个模型）+ 残差
    fig, axes = plt.subplots(2, 1, figsize=(11, 5.6), dpi=150, sharex=True,
                             gridspec_kw={"height_ratios": [2, 1]})
    axes[0].plot(test.index, yte.values, color="#404040", lw=1.4, label="真实 GMV")
    colors = {"LinearRegression": "#2E75B6"}
    for name, p in preds_store.items():
        axes[0].plot(test.index, p, lw=1.2, ls="--",
                     color=colors.get(name, "#ED7D31"), label=f"{name} 预测")
    axes[0].set_ylabel("GMV（元）")
    axes[0].legend(fontsize=8)
    axes[0].grid(alpha=0.25)
    axes[0].set_title(f"测试集（后 {len(test)} 天）预测效果对比", fontsize=12, color="#1F3864")
    axes[1].bar(test.index, yte.values - preds_store[best_name], color="#A5A5A5", width=0.8)
    axes[1].axhline(0, color="#C00000", lw=0.8)
    axes[1].set_ylabel("残差")
    axes[1].grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "fig_model_eval.png")
    plt.close(fig)

    # ---------------- 落盘 ----------------
    fut_df = pd.DataFrame({
        "date": fut.index.strftime("%Y-%m-%d"),
        "pred_gmv": np.round(fut.values, 2),
        "weekday": [["周一", "周二", "周三", "周四", "周五", "周六", "周日"][d.day_of_week] for d in fut.index],
    })
    fut_df.to_csv(OUT_DIR / "forecast_7d.csv", index=False, encoding="utf-8-sig")

    summary = {
        "data": stats,
        "split": {
            "train_days": int(len(train)),
            "test_days": int(len(test)),
            "train_range": [str(train.index.min().date()), str(train.index.max().date())],
            "test_range": [str(test.index.min().date()), str(test.index.max().date())],
        },
        "models": results,
        "tree_model_used": used_tree,
        "baseline_lag1": baseline,
        "best_model": best_name,
        "feature_importance_top8": importances,
        "forecast_7d": fut_df.to_dict(orient="records"),
        "forecast_sum": round(float(fut.sum()), 2),
        "forecast_mean": round(float(fut.mean()), 2),
        "resid_std": round(float(resid.std()), 2),
    }
    with open(OUT_DIR / "prediction_metrics.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # ---------------- 控制台输出 ----------------
    print("\n=== 数据概况 ===")
    print(f"天数 {stats['days']}，GMV 合计 {stats['gmv_total']:,.2f}，均值 {stats['gmv_mean']:,.2f}，"
          f"标准差 {stats['gmv_std']:,.2f}，变异系数 {stats['gmv_cv']}")
    print("周内均值：", stats["weekday_mean"])
    print(f"周内振幅 {stats['weekday_amplitude']:,.2f} 元（占日均 {stats['weekday_amplitude_pct']}%）")
    print(f"周同比均值 {stats['wow_mean']:+.2%}，月环比均值 {stats['month_mom_mean']:+.2%}"
          f"（波动 σ={stats['month_mom_std']:.2%}）")

    print("\n=== 模型评估（训练/测试按时间切分，不打乱） ===")
    for name, r in results.items():
        print(f"{name:20s} 训练 MAE={r['train']['MAE']:>10,.0f} MAPE={r['train']['MAPE']:>5.1f}% | "
              f"测试 MAE={r['test']['MAE']:>10,.0f} RMSE={r['test']['RMSE']:>10,.0f} MAPE={r['test']['MAPE']:>5.1f}%")
    print(f"{'基线(lag1)':20s} 测试 MAE={baseline['MAE']:>10,.0f} RMSE={baseline['RMSE']:>10,.0f} MAPE={baseline['MAPE']:>5.1f}%")
    print(f"\n最优模型：{best_name}；树模型实现：{used_tree}")
    if importances:
        print("特征重要度 Top8：")
        for k, v in importances.items():
            print(f"  {k:12s} {v:.4f}")

    print("\n=== 未来 7 天 GMV 预测 ===")
    for _, r in fut_df.iterrows():
        print(f"  {r['date']}  {r['weekday']}  {r['pred_gmv']:>12,.2f}")
    print(f"  合计 {summary['forecast_sum']:,.2f} 元，日均 {summary['forecast_mean']:,.2f} 元")
    print(f"\n输出目录：{OUT_DIR}")


if __name__ == "__main__":
    sys.exit(main())
