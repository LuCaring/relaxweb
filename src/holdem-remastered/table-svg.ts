/** Scene artwork stays as live SVG inside Phaser DOMElements, never a texture. */
const SVG_NS = 'http://www.w3.org/2000/svg';
let nextId = 0;

function wrapper(className: string, width: number, height: number, markup: string): HTMLDivElement {
  const node = document.createElement('div');
  node.className = className;
  node.style.width = `${width}px`;
  node.style.height = `${height}px`;
  node.style.pointerEvents = 'none';
  node.setAttribute('aria-hidden', 'true');
  node.innerHTML = markup;
  return node;
}

export function tableArtwork(width: number, height: number): HTMLDivElement {
  const id = `poker-vectors-${++nextId}`;
  return wrapper('poker-remastered-table-art', width, height, `
    <svg xmlns="${SVG_NS}" viewBox="0 0 1200 650" width="100%" height="100%" preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <radialGradient id="${id}-room"><stop stop-color="#213e47"/><stop offset=".55" stop-color="#122736"/><stop offset="1" stop-color="#0a1726"/></radialGradient>
        <linearGradient id="${id}-rail" x1="0" y1="0" x2="0" y2="1">
          <stop stop-color="#e1c990"/><stop offset=".28" stop-color="#a77844"/><stop offset=".65" stop-color="#75502f"/><stop offset="1" stop-color="#d0aa6d"/>
        </linearGradient>
        <radialGradient id="${id}-felt" cx="50%" cy="39%" r="72%">
          <stop stop-color="#11695b"/><stop offset=".55" stop-color="#0a544c"/><stop offset="1" stop-color="#073c3b"/>
        </radialGradient>
        <pattern id="${id}-grid" width="54" height="54" patternUnits="userSpaceOnUse">
          <path d="M54 0 H0 V54" fill="none" stroke="#b2d1cb" stroke-opacity=".055" stroke-width="1"/>
        </pattern>
        <pattern id="${id}-weave" width="9" height="9" patternUnits="userSpaceOnUse">
          <path d="M0 9 L9 0 M-2 2 L2 -2 M7 11 L11 7" stroke="#d9f2d8" stroke-opacity=".028" stroke-width="1"/>
        </pattern>
      </defs>
      <rect width="1200" height="650" fill="url(#${id}-room)"/>
      <rect width="1200" height="650" fill="url(#${id}-grid)"/>
      <ellipse cx="600" cy="50" rx="560" ry="150" fill="#547a7b" opacity=".07"/>
      <rect x="87" y="105" width="1026" height="475" rx="236" fill="#030c13" opacity=".50"/>
      <rect x="87" y="86" width="1026" height="475" rx="236" fill="#463322"/>
      <rect x="92" y="89" width="1016" height="466" rx="232" fill="url(#${id}-rail)"/>
      <rect x="104" y="100" width="992" height="444" rx="222" fill="#263d3c" stroke="#eddaa8" stroke-opacity=".42" stroke-width="2"/>
      <rect x="120" y="116" width="960" height="412" rx="205" fill="url(#${id}-felt)"/>
      <rect x="120" y="116" width="960" height="412" rx="205" fill="url(#${id}-weave)"/>
      <rect x="140" y="136" width="920" height="372" rx="185" fill="none" stroke="#b2d9c5" stroke-opacity=".26" stroke-width="2"/>
      <rect x="158" y="154" width="884" height="336" rx="168" fill="none" stroke="#d8e9d7" stroke-opacity=".09" stroke-width="1" stroke-dasharray="3 7"/>
    </svg>`);
}

export function turnRingArtwork(size: number): HTMLDivElement {
  return wrapper('poker-remastered-turn-ring', size, size, `
    <svg xmlns="${SVG_NS}" viewBox="0 0 72 72" width="100%" height="100%" aria-hidden="true">
      <circle cx="36" cy="36" r="31" fill="none" stroke="#aaf5c6" stroke-opacity=".86" stroke-width="2"/>
      <circle cx="36" cy="36" r="26" fill="none" stroke="#aaf5c6" stroke-opacity=".22" stroke-width="4"/>
    </svg>`);
}

export function chipArtwork(size: number): HTMLDivElement {
  return wrapper('poker-remastered-flying-chip', size, size, `
    <svg xmlns="${SVG_NS}" viewBox="0 0 40 40" width="100%" height="100%" aria-hidden="true">
      <ellipse cx="20" cy="30" rx="17" ry="6" fill="#674024"/><path d="M3 18 V29 Q20 41 37 29 V18" fill="#c59454" stroke="#f8e5b3" stroke-width="2"/>
      <ellipse cx="20" cy="18" rx="17" ry="8" fill="#e4bf7b" stroke="#fff1ca" stroke-width="2"/>
      <ellipse cx="20" cy="18" rx="10" ry="4.5" fill="none" stroke="#fff4d4" stroke-opacity=".8" stroke-width="2"/>
    </svg>`);
}

const CHIP_COLORS = ['#d2ae70', '#426f71', '#a56254', '#d6d5bd', '#426086'];

function chipColumn(x: number, count: number, color: string): string {
  let svg = '';
  for (let layer = 0; layer < count; layer += 1) {
    const y = 75 - layer * 8;
    svg += `<path d="M${x - 15} ${y - 5}v6c0 8 30 8 30 0v-6" fill="${color}" stroke="#213d3c" stroke-width="1.5"/>
      <ellipse cx="${x}" cy="${y - 5}" rx="15" ry="6" fill="${color}" stroke="#f9e9bb" stroke-width="1.4"/>
      <ellipse cx="${x}" cy="${y - 5}" rx="8" ry="3" fill="none" stroke="#fff8db" stroke-opacity=".75" stroke-width="1"/>`;
  }
  return svg;
}

/** Five deliberately different quantities, represented by separate live SVG designs. */
export function chipStackArtwork(tier: number, width: number, height: number): HTMLDivElement {
  const node = wrapper('poker-remastered-pot-chips', width, height, '');
  setChipStack(node, tier);
  return node;
}

export function setChipStack(node: HTMLDivElement, tier: number): void {
  const level = Math.max(0, Math.min(5, Math.floor(tier)));
  node.dataset.tier = String(level);
  if (!level) { node.innerHTML = ''; return; }
  const columns = [0, 1, 2, 3, 4, 5][level];
  const spacing = columns === 1 ? 0 : 31;
  const first = 80 - (columns - 1) * spacing / 2;
  let stacks = '';
  for (let i = 0; i < columns; i += 1) {
    stacks += chipColumn(first + i * spacing, 2 + level + (i % 2), CHIP_COLORS[i % CHIP_COLORS.length]);
  }
  node.innerHTML = `<svg xmlns="${SVG_NS}" viewBox="0 0 160 96" width="100%" height="100%" aria-hidden="true">
    <ellipse cx="80" cy="82" rx="${22 + columns * 17}" ry="8" fill="#002b29" opacity=".52"/>${stacks}</svg>`;
}

export function transactionArtwork(amount: number, count: number, direction: 'bet' | 'payout', width: number): HTMLDivElement {
  const node = wrapper('poker-remastered-chip-flight', width, width * .6, '');
  node.dataset.direction = direction;
  node.dataset.amount = String(amount);
  const chips = Array.from({ length: count }, (_, i) => {
    const x = 50 + (i - (count - 1) / 2) * 16, y = 34 - (i % 2) * 8;
    return `<ellipse cx="${x}" cy="${y + 5}" rx="13" ry="5" fill="#6b442c"/>
      <ellipse cx="${x}" cy="${y}" rx="13" ry="6" fill="${CHIP_COLORS[i % CHIP_COLORS.length]}" stroke="#ffebc2" stroke-width="2"/>
      <ellipse cx="${x}" cy="${y}" rx="7" ry="3" fill="none" stroke="#fff8e1" stroke-width="1"/>`;
  }).join('');
  node.innerHTML = `<svg xmlns="${SVG_NS}" viewBox="0 0 100 60" width="100%" height="100%" aria-hidden="true">${chips}</svg>`;
  const badge = document.createElement('span');
  badge.className = 'poker-remastered-chip-amount';
  badge.textContent = `${direction === 'payout' ? '+' : '−'}${amount.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`;
  node.append(badge);
  return node;
}

export function sparkArtwork(size: number, warm: boolean): HTMLDivElement {
  return wrapper('poker-remastered-spark', size, size, `
    <svg xmlns="${SVG_NS}" viewBox="0 0 24 24" width="100%" height="100%" aria-hidden="true">
      <path d="M12 0 L15.5 8.5 L24 12 L15.5 15.5 L12 24 L8.5 15.5 L0 12 L8.5 8.5 Z" fill="${warm ? '#ffe1a1' : '#a4f0d3'}"/>
    </svg>`);
}
