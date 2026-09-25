"use strict";

import { estateRequest } from "./protocol.js";

const format = (value, digits = 2) => Number(value || 0).toLocaleString("zh-CN", {
  minimumFractionDigits: digits, maximumFractionDigits: digits,
});

function marketChart(history) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 420 160"); svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "最近采样的模拟指数走势");
  const prices = history.map((point) => Number(point.price));
  const low = Math.min(...prices); const high = Math.max(...prices);
  const range = Math.max(1, high - low);
  const firstTime = Number(history[0].time);
  const timeSpan = Math.max(1, Number(history.at(-1).time) - firstTime);
  const points = prices.map((price, index) => {
    const x = 8 + (Number(history[index].time) - firstTime) * 404 / timeSpan;
    const y = 145 - (price - low) / range * 130;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
  line.setAttribute("points", points.join(" "));
  line.setAttribute("fill", "none"); line.setAttribute("stroke", "#2d815f");
  line.setAttribute("stroke-width", "3"); line.setAttribute("stroke-linejoin", "round");
  svg.append(line);
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
  if (market.history.length) wrap.append(marketChart(market.history));
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
  const estimate = document.createElement("p"); estimate.className = "estate-market-disclosure";
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
