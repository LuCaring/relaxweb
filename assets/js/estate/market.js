"use strict";

import { estateRequest } from "./protocol.js";

const format = (value, digits = 2) => Number(value || 0).toLocaleString("zh-CN", {
  minimumFractionDigits: digits, maximumFractionDigits: digits,
});
const PERIODS = [["minute", "分钟"], ["hour", "小时"], ["day", "日"]];
let selectedPeriod = "minute";

function marketChart(candles, period) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 720 240"); svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `${PERIODS.find(([key]) => key === period)[1]}K线图`);
  const ns = "http://www.w3.org/2000/svg";
  const low = Math.min(...candles.map((item) => Number(item.low)));
  const high = Math.max(...candles.map((item) => Number(item.high)));
  const padding = Math.max((high - low) * 0.08, high * 0.001);
  const bottom = low - padding; const range = high - low + padding * 2;
  const y = (value) => 194 - (value - bottom) / range * 170;
  const add = (tag, attrs) => {
    const node = document.createElementNS(ns, tag);
    for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, String(value));
    svg.append(node); return node;
  };
  for (const value of [high, (high + low) / 2, low]) {
    const ordinate = y(value);
    add("line", {x1: 54, x2: 706, y1: ordinate, y2: ordinate, stroke: "#dccbaa", "stroke-dasharray": "3 4"});
    const label = add("text", {x: 47, y: ordinate + 4, "text-anchor": "end", fill: "#7d674c", "font-size": 11});
    label.textContent = format(value);
  }
  const step = 646 / candles.length;
  const bodyWidth = Math.max(2, Math.min(12, step * 0.68));
  candles.forEach((item, index) => {
    const x = 58 + step * (index + 0.5);
    const rising = item.close >= item.open;
    const color = rising ? "#b95245" : "#358266";
    add("line", {x1: x, x2: x, y1: y(item.high), y2: y(item.low), stroke: color, "stroke-width": 1.5});
    const top = Math.min(y(item.open), y(item.close));
    const body = add("rect", {x: x - bodyWidth / 2, y: top, width: bodyWidth,
      height: Math.max(2, Math.abs(y(item.open) - y(item.close))), fill: color});
    const title = document.createElementNS(ns, "title");
    title.textContent = `${new Date(item.time * 1000).toLocaleString("zh-CN")}  开 ${format(item.open)}  高 ${format(item.high)}  低 ${format(item.low)}  收 ${format(item.close)}`;
    body.append(title);
  });
  for (const index of new Set([0, Math.floor((candles.length - 1) / 2), candles.length - 1])) {
    const item = candles[index];
    const label = add("text", {x: 58 + step * (index + 0.5), y: 225,
      "text-anchor": index === 0 ? "start" : index === candles.length - 1 ? "end" : "middle",
      fill: "#7d674c", "font-size": 11});
    label.textContent = new Date(item.time * 1000).toLocaleString("zh-CN", period === "day"
      ? {month: "numeric", day: "numeric"} : {month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit"});
  }
  return svg;
}

export function renderMarketGame(target, market, onTrade, notice = "") {
  const wrap = document.createElement("div"); wrap.className = "estate-market";
  if (!market) {
    const loading = document.createElement("p"); loading.className = "estate-sheet-note";
    loading.textContent = "正在获取模拟指数…"; wrap.append(loading); target.append(wrap); return;
  }
  const title = document.createElement("div"); title.className = "estate-market-quote";
  const name = document.createElement("strong"); name.textContent = market.name;
  const price = document.createElement("b"); price.textContent = `${format(market.price)} 点`;
  title.append(name, price); wrap.append(title);
  const status = document.createElement("p"); status.className = "estate-sheet-note";
  status.textContent = market.available
    ? `24 小时模拟行情 · ${market.source_kind === "simulated" ? "外部报价中断，当前使用游戏内模拟走势" : "参考 SOL/USD"} · 每分钟更新 · 买卖各收取 ${(market.fee_rate * 100).toFixed(1)}% 手续费`
    : "参考行情暂不可用，交易已暂停；可查看已有持仓。";
  wrap.append(status);
  const chartControls = document.createElement("div"); chartControls.className = "estate-market-periods";
  const chart = document.createElement("div"); chart.className = "estate-market-chart";
  const drawChart = () => {
    const candles = market.candles?.[selectedPeriod] || [];
    chart.replaceChildren();
    if (candles.length) chart.append(marketChart(candles, selectedPeriod));
    else chart.textContent = "暂无这一周期的采样记录。";
    for (const control of chartControls.children) {
      control.setAttribute("aria-pressed", String(control.dataset.period === selectedPeriod));
    }
  };
  for (const [period, label] of PERIODS) {
    const control = document.createElement("button"); control.type = "button";
    control.className = "estate-button"; control.dataset.period = period;
    control.textContent = `${label}K线`;
    control.addEventListener("click", () => { selectedPeriod = period; drawChart(); });
    chartControls.append(control);
  }
  wrap.append(chartControls, chart);
  drawChart();
  const chartNote = document.createElement("p"); chartNote.className = "estate-market-disclosure";
  chartNote.textContent = "K 线由每分钟的服务器报价汇总；红色上涨、绿色下跌。无采样的时段不会补造数据。";
  wrap.append(chartNote);
  const holdings = document.createElement("div"); holdings.className = "estate-market-holdings";
  for (const [label, value] of [
    ["持有份额", format(market.shares, 3)], ["当前市值", `${format(market.market_value)} 金币`],
    ["持仓成本", `${format(market.cost_basis)} 金币`],
    ["已实现盈亏", `${format(market.realized_pnl)} 金币`],
  ]) {
    const cell = document.createElement("span");
    cell.textContent = `${label}：${value}`; holdings.append(cell);
  }
  wrap.append(holdings);
  const form = document.createElement("div"); form.className = "estate-market-order";
  const input = document.createElement("input"); input.type = "number";
  input.min = "0.001"; input.step = "0.001"; input.placeholder = "份额，例如 0.5";
  input.setAttribute("aria-label", "交易份额");
  const buy = document.createElement("button"); buy.type = "button";
  buy.className = "estate-button estate-button-gold"; buy.textContent = "买入";
  const sell = document.createElement("button"); sell.type = "button";
  sell.className = "estate-button"; sell.textContent = "卖出";
  buy.disabled = !market.available; sell.disabled = !market.available || market.shares <= 0;
  const message = document.createElement("p"); message.className = "estate-market-message";
  message.setAttribute("role", "status");
  message.textContent = notice;
  const estimate = document.createElement("p"); estimate.className = "estate-market-disclosure estate-market-estimate";
  const updateEstimate = () => {
    const quantity = Number(input.value);
    estimate.textContent = Number.isFinite(quantity) && quantity > 0
      ? `估算买入支出 ${format(quantity * market.price * (1 + market.fee_rate))} 金币；卖出收入 ${format(quantity * market.price * (1 - market.fee_rate))} 金币。实际金额以成交时服务端报价为准。`
      : "请输入份额查看预计支出与收入；支持 0.001 份起交易。";
  };
  input.addEventListener("input", updateEstimate); updateEstimate();
  const submit = async (side) => {
    const quantity = input.value.trim();
    if (!/^\d+(?:\.\d{1,3})?$/.test(quantity) || Number(quantity) <= 0) {
      message.textContent = "请输入最多三位小数的正数份额。"; return;
    }
    buy.disabled = true; sell.disabled = true; message.textContent = "正在提交交易…";
    try {
      const result = await estateRequest("estate_market_trade", { side, quantity }, { timeoutMs: 15000 });
      onTrade(result);
    } catch (error) {
      message.textContent = error.message || "交易失败，请稍后重试。";
      buy.disabled = !market.available; sell.disabled = !market.available || market.shares <= 0;
    }
  };
  buy.addEventListener("click", () => submit("buy"));
  sell.addEventListener("click", () => submit("sell"));
  form.append(input, buy, sell); wrap.append(form, estimate, message);
  const disclosure = document.createElement("p"); disclosure.className = "estate-market-disclosure";
  disclosure.textContent = "此指数仅使用游戏金币交易，并非真实股票或加密资产；不支持借款、做空或提现。";
  wrap.append(disclosure); target.append(wrap);
}
