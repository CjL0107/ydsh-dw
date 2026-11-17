/* ============================================================
   云购生活 · 经营分析大屏  交互脚本
   ------------------------------------------------------------
   1) fitScreen：1920×1080 设计稿按 min(w/1920, h/1080) 整体缩放，
      窗口比例与设计稿不一致时留黑边，不拉伸变形（比起 rem 换算，
      canvas 图表用整体缩放最不容易出现"图表瘦了但字体没瘦"的错位）。
   2) 数据只请求一次 /api/overview：六个面板六次握手在弱网下很容易
      出现有的面板有数、有的空白，一次性拿全再分发，失败就整体降级。
   3) 每个面板独立 try/catch：某个图表配置报错不能把整块大屏搞白屏。
   ============================================================ */
'use strict';

const DESIGN_W = 1920;
const DESIGN_H = 1080;
const REFRESH_MS = 5 * 60 * 1000;   // 5 分钟自动刷新一次快照接口
// ?static=1 关闭数字滚动动画。截图和被投到大屏上时，动画会让截图抓到
// "2734 万" 的 78% 位置（实测抓到 2129 万），看起来像数据算错了。
const STATIC = new URLSearchParams(location.search).has('static');

const C = {
  cyan: '#22d3ee', blue: '#3b82f6', gold: '#fbbf24',
  purple: '#a78bfa', green: '#34d399', red: '#f87171',
  axis: '#3c5a7d', split: 'rgba(120,160,210,0.12)', dim: '#7c93b5', txt: '#dbeafe',
};

const charts = {};
let snapshot = null;

/* ---------------- 工具 ---------------- */
const $ = (id) => document.getElementById(id);

function wan(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  if (Math.abs(v) >= 1e8) return (v / 1e8).toFixed(digits) + ' 亿';
  if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(digits) + ' 万';
  return v.toFixed(digits);
}
function thousands(v) {
  if (v === null || v === undefined) return '—';
  return Math.round(v).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}
function num(v, digits = 0) {
  if (v === null || v === undefined) return '—';
  return v.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/* 数字滚动：答辩时这点动画能让"数据是活的"这件事被看见 */
function countUp(el, target, render, ms = 1100) {
  if (!Number.isFinite(target)) { el.textContent = '—'; return; }
  if (STATIC) { el.textContent = render(target); return; }
  const t0 = performance.now();
  const step = (now) => {
    const p = Math.min(1, (now - t0) / ms);
    const eased = 1 - Math.pow(1 - p, 3);   // easeOutCubic
    el.textContent = render(target * eased);
    if (p < 1) requestAnimationFrame(step);
    else el.textContent = render(target);
  };
  requestAnimationFrame(step);
}

/* ---------------- 自适应缩放 ---------------- */
function fitScreen() {
  const scale = Math.min(window.innerWidth / DESIGN_W, window.innerHeight / DESIGN_H);
  const screen = $('screen');
  screen.style.transform = `scale(${scale})`;
  // 缩放后元素仍占 1920×1080 的布局空间，用负边距把多余空间吃掉，保证真正居中
  screen.style.margin = `${-(DESIGN_H * (1 - scale)) / 2}px ${-(DESIGN_W * (1 - scale)) / 2}px`;
}

/* ---------------- 图表公共配置 ---------------- */
const tooltipBase = {
  backgroundColor: 'rgba(8, 20, 36, 0.95)',
  borderColor: 'rgba(34, 211, 238, 0.4)',
  borderWidth: 1,
  padding: [8, 12],
  textStyle: { color: '#dbeafe', fontSize: 12 },
  extraCssText: 'border-radius:4px;box-shadow:0 6px 20px rgba(0,0,0,.5);',
};

function axisCommon(extra = {}) {
  return Object.assign({
    axisLine: { lineStyle: { color: C.axis } },
    axisTick: { show: false },
    axisLabel: { color: C.dim, fontSize: 11 },
    splitLine: { lineStyle: { color: C.split } },
  }, extra);
}

function p95(values) {
  const arr = values.filter((v) => Number.isFinite(v)).sort((a, b) => a - b);
  if (!arr.length) return 1;
  return arr[Math.min(arr.length - 1, Math.floor(arr.length * 0.95))] || 1;
}

// 每个图表一个 ResizeObserver：跟着容器走。
// 为什么必须有：ECharts 只在 init 时读一次容器尺寸，之后容器变大它不会自己重算。
// 首屏里字体加载、flex 行高重新分配都会在中途改变 .chart 的高度，
// 于是底部两个面板（热力、散点）的画布被固定在 30/50 px，看起来就是一条细线。
// 之前只在 window.resize 里调用，窗口不变就永远不会触发，所以 1920×1080 下一直是坏的。
const _ros = {};

function initChart(id, option) {
  try {
    const dom = $(id);
    if (!dom) return;
    charts[id] = charts[id] || echarts.init(dom, null, { renderer: 'canvas' });
    charts[id].setOption(option, true);
    if (!_ros[id] && typeof ResizeObserver !== 'undefined') {
      _ros[id] = new ResizeObserver(() => {
        try { charts[id].resize(); } catch (_) { /* 图表未就绪时忽略 */ }
      });
      _ros[id].observe(dom);
    }
  } catch (e) {
    console.error('[chart]', id, e);
    fail(id, e);
  }
}

/* ---------------- 各面板 ---------------- */
function renderKpi(kpi) {
  // 副标题自己写短句，不用 ADS 层那句 30 多字的 definition —— 卡片宽度只有 300px，
  // definition 一定被截断成半句（"含已支付/已完成/退款中，实..."），比不写更难看
  const pick = [
    { key: 'gmv', label: '全年 GMV', unit: '万元', div: 1e4, digits: 2, color: C.cyan, sub: '含已支付 / 已完成 / 退款中' },
    { key: 'order_cnt', label: '有效订单量', unit: '单', div: 1, digits: 0, color: C.blue, sub: '不含取消与待支付' },
    { key: 'aov', label: '客单价 AOV', unit: '元/单', div: 1, digits: 2, color: C.gold, sub: 'GMV ÷ 有效订单量' },
    { key: 'active_users', label: '活跃用户数', unit: '人', div: 1, digits: 0, color: C.purple, sub: '全年有成交的用户' },
    { key: 'cancel_rate', label: '取消率', unit: '%', div: 1, digits: 2, color: C.red, sub: '取消单 ÷ 全部订单' },
    { key: 'category_top1_share', label: '品类集中度 TOP1', unit: '%', div: 1, digits: 2, color: C.green, sub: '第一大品类 GMV 占比' },
  ];
  const map = {};
  (kpi.items || []).forEach((it) => { map[it.key] = it; });

  const row = $('kpiRow');
  row.innerHTML = '';
  pick.forEach((p) => {
    const it = map[p.key] || {};
    const v = it.value;
    const card = document.createElement('div');
    card.className = 'kpi';
    card.style.setProperty('--accent', p.color);
    card.innerHTML = `
      <div class="label">${p.label}</div>
      <div class="value"><span class="n">—</span><small>${p.unit}</small></div>
      <div class="sub">${p.sub}</div>`;
    row.appendChild(card);
    const el = card.querySelector('.n');
    // 单位已写在卡片里，数字按 div 缩放后再滚动
    const val = v === null || v === undefined ? NaN : v / p.div;
    countUp(el, val, (x) => x.toLocaleString('zh-CN', {
      minimumFractionDigits: p.digits, maximumFractionDigits: p.digits,
    }));
  });
}

function renderTrend(t) {
  const dates = t.dates || [];
  const gmv = t.gmv || [];
  const ma7 = t.ma7 || [];
  const orders = t.orders || [];

  initChart('c-trend', {
    grid: { left: 58, right: 52, top: 34, bottom: 26 },
    tooltip: Object.assign({}, tooltipBase, {
      trigger: 'axis',
      axisPointer: { type: 'cross', lineStyle: { color: 'rgba(34,211,238,.4)' } },
      formatter: (ps) => {
        const d = ps[0].axisValue;
        const rows = ps.map((p) => {
          const unit = p.seriesName === '有效订单' ? ' 单' : ' 元';
          const v = p.seriesName === '有效订单' ? thousands(p.value) : num(p.value, 2);
          return `${p.marker}${p.seriesName}：<b style="color:${p.color}">${v}${unit}</b>`;
        });
        return `<div style="margin-bottom:4px;color:#9db9d8">${d}</div>` + rows.join('<br>');
      },
    }),
    legend: {
      right: 6, top: 2, itemWidth: 12, itemHeight: 8, itemGap: 14,
      textStyle: { color: C.dim, fontSize: 11 },
      data: ['GMV', '7 日均线', '有效订单'],
    },
    xAxis: Object.assign(axisCommon(), {
      type: 'category', data: dates, boundaryGap: false,
      axisLabel: { color: C.dim, fontSize: 10, hideOverlap: true },
    }),
    yAxis: [
      Object.assign(axisCommon(), {
        type: 'value',
        axisLabel: { color: C.dim, fontSize: 10, formatter: (v) => (v >= 1e4 ? (v / 1e4) + '万' : v) },
      }),
      Object.assign(axisCommon(), {
        type: 'value', splitLine: { show: false },
        axisLabel: { color: C.dim, fontSize: 10 },
      }),
    ],
    series: [
      {
        name: 'GMV', type: 'line', data: gmv, smooth: true, showSymbol: false,
        lineStyle: { width: 2, color: C.cyan },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(34,211,238,0.42)' },
            { offset: 1, color: 'rgba(34,211,238,0.02)' },
          ]),
        },
      },
      {
        name: '7 日均线', type: 'line', data: ma7, smooth: true, showSymbol: false,
        lineStyle: { width: 1.6, color: C.gold, type: 'dashed' },
      },
      {
        name: '有效订单', type: 'bar', yAxisIndex: 1, data: orders,
        barWidth: '46%',
        itemStyle: { color: 'rgba(59,130,246,0.34)', borderRadius: [2, 2, 0, 0] },
      },
    ],
  });
}

function renderCategory(cat) {
  const items = (cat.items || []).slice().sort((a, b) => (a.gmv || 0) - (b.gmv || 0));
  initChart('c-category', {
    grid: { left: 8, right: 62, top: 8, bottom: 4, containLabel: true },
    tooltip: Object.assign({}, tooltipBase, {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      formatter: (ps) => {
        const i = items[ps[0].dataIndex];
        return `<b>${i.name}</b><br/>GMV：${num(i.gmv, 2)} 元<br/>占比：${i.share}%<br/>订单：${thousands(i.orders)} 单<br/>件单价：${num(i.avgPrice, 2)} 元`;
      },
    }),
    xAxis: Object.assign(axisCommon(), {
      type: 'value', splitLine: { lineStyle: { color: C.split } },
      axisLabel: { color: C.dim, fontSize: 10, formatter: (v) => (v >= 1e4 ? v / 1e4 + '万' : v) },
    }),
    yAxis: Object.assign(axisCommon(), {
      type: 'category', data: items.map((i) => i.name),
      axisLabel: { color: '#b9d2ec', fontSize: 11 }, splitLine: { show: false },
    }),
    series: [{
      type: 'bar', data: items.map((i) => i.gmv), barWidth: 12,
      itemStyle: {
        borderRadius: [0, 6, 6, 0],
        color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
          { offset: 0, color: 'rgba(34,211,238,0.25)' },
          { offset: 1, color: C.cyan },
        ]),
      },
      label: {
        show: true, position: 'right', color: '#9ec9e8', fontSize: 11,
        formatter: (p) => `${items[p.dataIndex].share}%`,
      },
    }],
  });
}

function renderChannel(ch) {
  const items = ch.items || [];
  const total = items.reduce((s, i) => s + (i.gmv || 0), 0);
  const colors = [C.cyan, C.blue, C.purple, C.gold, C.green];
  initChart('c-channel', {
    tooltip: Object.assign({}, tooltipBase, {
      formatter: (p) => `<b>${p.name}</b><br/>GMV：${num(p.value, 2)} 元<br/>占比：${p.percent}%<br/>客单价：${num(items[p.dataIndex].aov, 2)} 元`,
    }),
    legend: {
      bottom: 2, itemWidth: 9, itemHeight: 9, itemGap: 14,
      textStyle: { color: C.dim, fontSize: 11 },
      // 4 个扇区在窄面板里放不下环外标签，占比并进图例
      formatter: (n) => {
        const it = items.find((x) => x.name === n);
        return it ? `${n}  ${it.share}%` : n;
      },
    },
    series: [{
      type: 'pie', radius: ['46%', '68%'], center: ['50%', '44%'],
      avoidLabelOverlap: true,
      itemStyle: { borderColor: '#0a1526', borderWidth: 3 },
      label: { show: false },
      data: items.map((i, idx) => ({
        name: i.name, value: i.gmv,
        itemStyle: { color: colors[idx % colors.length] },
      })),
    }],
    graphic: [{
      type: 'text', left: 'center', top: '39%',
      style: {
        text: wan(total), fill: '#e8f4ff', fontSize: 17,
        fontFamily: 'Consolas', fontWeight: 700, textAlign: 'center',
      },
    }, {
      type: 'text', left: 'center', top: '47%',
      style: { text: '渠道 GMV 合计', fill: '#6b8bb3', fontSize: 11, textAlign: 'center' },
    }],
  });
}

function renderPayment(pay) {
  const items = pay.items || [];
  const colors = [C.green, C.blue, C.gold, C.purple];
  initChart('c-payment', {
    tooltip: Object.assign({}, tooltipBase, {
      formatter: (p) => `<b>${p.name}</b><br/>金额：${num(p.value, 2)} 元<br/>占比：${p.percent}%<br/>订单：${thousands(items[p.dataIndex].orders)} 单<br/>客单价：${num(items[p.dataIndex].aov, 2)} 元`,
    }),
    legend: {
      bottom: 2, itemWidth: 9, itemHeight: 9, itemGap: 12,
      textStyle: { color: C.dim, fontSize: 11 },
      formatter: (n) => {
        const it = items.find((x) => x.name === n);
        const total = items.reduce((a, b) => a + (b.gmv || 0), 0) || 1;
        return it ? `${n}  ${((it.gmv / total) * 100).toFixed(1)}%` : n;
      },
    },
    series: [{
      type: 'pie', roseType: 'radius',
      radius: ['32%', '66%'], center: ['50%', '44%'],
      itemStyle: { borderColor: '#0a1526', borderWidth: 2, borderRadius: 4 },
      label: { show: false },
      data: items.map((i, idx) => ({
        name: i.name, value: i.gmv, itemStyle: { color: colors[idx % colors.length] },
      })),
    }],
    graphic: [{
      type: 'text', left: 'center', top: '38%',
      style: {
        text: `${items.reduce((a, b) => a + (b.orders || 0), 0).toLocaleString('zh-CN')}`,
        fill: '#e8f4ff', fontSize: 18, fontFamily: 'Consolas', fontWeight: 700, textAlign: 'center',
      },
    }, {
      type: 'text', left: 'center', top: '46%',
      style: { text: '成交订单合计', fill: '#6b8bb3', fontSize: 10, textAlign: 'center' },
    }],
  });
}

function renderMap(prov, cats) {
  initChart('c-map', {
    tooltip: Object.assign({}, tooltipBase, {
      formatter: (p) => {
        if (!p.value || Number.isNaN(p.value)) return `${p.name}<br/><span style="color:#7c93b5">无销售数据</span>`;
        const it = mapIndex[p.name];
        return `<b>${p.name}</b><br/>GMV：${num(p.value, 2)} 元` +
          (it ? `<br/>占比：${it.share}%<br/>订单：${thousands(it.orders)} 单<br/>客单价：${num(it.aov, 2)} 元` : '');
      },
    }),
    visualMap: {
      min: 0, max: cats, left: 8, bottom: 6, itemWidth: 9, itemHeight: 62,
      text: ['高', '低'], textStyle: { color: C.dim, fontSize: 10 },
      calculable: true, precision: 0,
      inRange: { color: ['#0b2138', '#12507a', '#1d86a8', '#22d3ee', '#fbbf24'] },
    },
    series: [{
      type: 'map', map: 'china', roam: false,
      // 面板只有 5/12 宽，用 zoom 会把地图缩到中间一小块，
      // 改成按容器百分比布局，才能把可用高度吃满。
      // layoutSize 实测标定：108% 只占一半高度、135% 基本吃满不裁边、
      // 160% 开始压到边界。取 140% —— 高度用满，四周仍留 8px 左右的呼吸位。
      layoutCenter: ['52%', '50%'], layoutSize: '140%',
      label: {
        show: true, fontSize: 8, color: 'rgba(200,222,245,0.65)',
        // 34 个省全标会糊成一片，只标有销售数据的 10 个
        formatter: (p) => (mapIndex[p.name] ? p.name.replace(/(省|市|自治区|维吾尔|壮族|回族|特别行政区)/g, '') : ''),
      },
      itemStyle: {
        areaColor: '#0b2138',
        borderColor: 'rgba(80,150,200,0.45)',
        borderWidth: 0.7,
      },
      emphasis: {
        label: { show: true, color: '#fff', fontSize: 10 },
        itemStyle: { areaColor: '#fbbf24', borderColor: '#fff', borderWidth: 1 },
      },
      data: provItems,
    }],
  });
}

let mapIndex = {};
let provItems = [];

function renderHeat(h) {
  const cats = h.categories || [];
  const hours = h.hours || [];
  initChart('c-heat', {
    grid: { left: 62, right: 16, top: 10, bottom: 52 },
    tooltip: Object.assign({}, tooltipBase, {
      formatter: (p) => `${hours[p.value[0]]}:00　${cats[p.value[1]]}<br/>GMV：<b style="color:#22d3ee">${num(p.value[2], 2)}</b> 元`,
    }),
    xAxis: Object.assign(axisCommon(), {
      type: 'category', data: hours, splitArea: { show: false },
      axisLabel: { color: C.dim, fontSize: 10, interval: 1 },
    }),
    yAxis: Object.assign(axisCommon(), {
      type: 'category', data: cats, splitLine: { show: false },
      axisLabel: { color: '#b9d2ec', fontSize: 11 },
    }),
    visualMap: {
      // 直接拿最大值当上界，一个 300 万的格子会把其余 190 格压成一片同色。
      // 取 P95 做上界，并把超出部分截断到最高色，层次才分得开。
      min: 0, max: p95((h.data || []).map((d) => d[2])),
      orient: 'horizontal', left: 'center', bottom: 2, itemWidth: 10, itemHeight: 70,
      textStyle: { color: C.dim, fontSize: 10 }, calculable: true,
      inRange: { color: ['#0a1c30', '#0f4a6b', '#1c8fa8', '#22d3ee', '#fde68a'] },
    },
    series: [{
      type: 'heatmap', data: h.data || [],
      itemStyle: { borderColor: 'rgba(5,10,20,0.6)', borderWidth: 1 },
      emphasis: { itemStyle: { borderColor: '#fff', borderWidth: 1 } },
    }],
  });
}

function renderScatter(sc) {
  const items = sc.items || [];
  const maxGmv = Math.max.apply(null, items.map((i) => i.gmv || 0)) || 1;
  // 只标 GMV 前 5 的城市名：36 个点全标会糊成一团，标注本身就成了噪声
  const top3 = new Set(items.slice().sort((a, b) => (b.gmv || 0) - (a.gmv || 0)).slice(0, 3).map((i) => i.name));
  items.forEach((i) => { i.rank5 = top3.has(i.name); });
  initChart('c-scatter', {
    grid: { left: 52, right: 16, top: 14, bottom: 34 },
    tooltip: Object.assign({}, tooltipBase, {
      formatter: (p) => {
        const i = items[p.dataIndex];
        return `<b>${i.name}</b><br/>单量：${thousands(i.orders)} 单<br/>客单价：${num(i.aov, 2)} 元<br/>GMV：${num(i.gmv, 2)} 元`;
      },
    }),
    xAxis: Object.assign(axisCommon(), {
      type: 'value', name: '单量', nameTextStyle: { color: '#5d7ba1', fontSize: 10 },
      axisLabel: { color: C.dim, fontSize: 10, formatter: (v) => (v >= 1000 ? v / 1000 + 'k' : v) },
    }),
    yAxis: Object.assign(axisCommon(), {
      type: 'value', name: '客单价', nameTextStyle: { color: '#5d7ba1', fontSize: 10 },
      scale: true,
      axisLabel: { color: C.dim, fontSize: 10 },
    }),
    series: [{
      type: 'scatter',
      data: items.map((i) => [i.orders, i.aov, i.gmv, i.name]),
      symbolSize: (val) => 8 + 22 * Math.sqrt((val[2] || 0) / maxGmv),
      itemStyle: {
        color: new echarts.graphic.RadialGradient(0.4, 0.3, 0.9, [
          { offset: 0, color: 'rgba(34,211,238,0.95)' },
          { offset: 1, color: 'rgba(34,211,238,0.18)' },
        ]),
        borderColor: 'rgba(34,211,238,0.9)', borderWidth: 1,
      },
      label: {
        show: true, position: 'top', color: '#9ec9e8', fontSize: 10, offset: [0, -6],
        // 只标 GMV 前 5 的城市，全标会糊成一团
        formatter: (p) => (items[p.dataIndex].rank5 ? p.value[3] : ''),
      },
    }],
  });
}

function renderStore(st) {
  const items = st.items || [];
  const max = Math.max.apply(null, items.map((i) => i.gmv || 0)) || 1;
  const ol = $('storeRank');
  ol.innerHTML = items.map((i) => `
    <li>
      <span class="no">${i.rank}</span>
      <span class="nm" title="${i.name}">${i.name}</span>
      <span class="city">${i.city}</span>
      <span class="val">${wan(i.gmv)}</span>
      <span class="bar" style="width:${Math.max(6, (i.gmv / max) * 100)}%"></span>
    </li>`).join('');
}

/* ---------------- 数据加载 ---------------- */
function fail(where, e) {
  const box = $('err');
  box.hidden = false;
  box.textContent = `数据渲染失败（${where}）\n${e && e.message ? e.message : e}\n\n` +
    `排查顺序：1) 接口是否 200：curl /api/health　2) 快照是否存在：apps/api/data/snapshot.json　` +
    `3) 浏览器控制台是否报跨域`;
}

async function load() {
  try {
    const resp = await fetch('/api/overview', { cache: 'no-store' });
    if (!resp.ok) throw new Error(`接口返回 ${resp.status} ${resp.statusText}`);
    const data = await resp.json();
    if (!data.trend || !data.trend.dates) throw new Error('返回体缺少 trend 面板，疑似快照不完整');

    snapshot = data;
    $('err').hidden = true;

    // 地图数据要先注册，再初始化图表
    if (!echarts.getMap('china')) {
      const geo = await fetch('vendor/china.json').then((r) => r.json());
      echarts.registerMap('china', geo);
    }
    const prov = (data.province && data.province.items) || [];
    mapIndex = {};
    prov.forEach((p) => { mapIndex[p.name] = p; });
    provItems = prov.map((p) => ({ name: p.name, value: p.gmv }));
    const cats = Math.max.apply(null, provItems.map((p) => p.value || 0)) || 1;

    renderKpi(data.kpi || {});
    renderTrend(data.trend || {});
    renderCategory(data.category || {});
    renderMap(prov, cats);
    renderChannel(data.channel || {});
    renderPayment(data.payment || {});
    renderHeat(data.hour_category || {});
    renderScatter(Object.assign({ items: [] }, data.city_scatter || {}));
    renderStore(data.store_top || {});

    const meta = data.meta || {};
    $('fresh').textContent = `数据更新 ${(meta.generated_at || '—').slice(5)}　·　后端 ${meta.backend || '-'}`;
  } catch (e) {
    console.error(e);
    fail('接口 /api/overview', e);
  }
}

/* ---------------- 启动 ---------------- */
function tick() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  $('clock').textContent = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

window.addEventListener('resize', () => {
  fitScreen();
  // 缩放只影响容器，图表内部像素不变，无需 resize；但窗口尺寸变化过大时
  // 仍然调用一次，避免 ECharts 认为容器尺寸被外部改过而放弃重绘
  Object.values(charts).forEach((c) => { try { c.resize(); } catch (_) {} });
});

fitScreen();
tick();
setInterval(tick, 1000);
load();
setInterval(load, REFRESH_MS);
