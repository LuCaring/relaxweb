/* 金币数额的展示格式：千位以上加英文逗号，两个页面与各模块共用。 */

const grouped = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const groupedCoins = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2, maximumFractionDigits: 2,
});

/** 余额、注额等记账数额：固定两位小数，如 1,234.56。 */
export function formatCoins(value) {
  return groupedCoins.format(Number(value || 0));
}

/** 价格、奖档、底注等整数数额：不补小数位，如 10,000。 */
export function formatCoinsWhole(value) {
  return grouped.format(Number(value || 0));
}
