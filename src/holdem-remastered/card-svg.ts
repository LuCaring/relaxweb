import type { Card } from './types';
import { DECK } from './deck';

const SUITS = ['S', 'H', 'D', 'C'] as const;
let nextInstance = 0;

function faceOf(card: Card | null): string {
  if (!card) return '1B';
  const rank = card.r === 10 ? 'T' : card.r === 11 ? 'J' : card.r === 12 ? 'Q'
    : card.r === 13 ? 'K' : card.r === 14 ? 'A' : String(card.r);
  const suit = SUITS[card.s];
  const face = `${rank}${suit}`;
  return DECK[face] ? face : '1B';
}

/** Preserve the vendor drawing while keeping each instance's SVG references local. */
function scopeIds(svg: SVGSVGElement): void {
  const prefix = `rw-card-${++nextInstance}-`;
  const ids = new Map<string, string>();
  for (const element of svg.querySelectorAll('[id]')) {
    const old = element.id;
    const scoped = `${prefix}${old}`;
    ids.set(old, scoped);
    element.id = scoped;
  }
  if (!ids.size) return;
  for (const element of svg.querySelectorAll('*')) {
    for (const attribute of [...element.attributes]) {
      const next = attribute.value
        .replace(/url\(#([^)]*)\)/g, (whole: string, id: string) => ids.has(id) ? `url(#${ids.get(id)})` : whole)
        .replace(/^#(.+)$/, (whole: string, id: string) => ids.has(id) ? `#${ids.get(id)}` : whole);
      if (next !== attribute.value) {
        if (attribute.namespaceURI) element.setAttributeNS(attribute.namespaceURI, attribute.name, next);
        else element.setAttribute(attribute.name, next);
      }
    }
  }
}

/** Replace only the SVG inside a stable Phaser DOMElement wrapper. */
export function setCardFace(node: HTMLDivElement, card: Card | null): void {
  if (card) {
    node.dataset.rank = String(card.r);
    node.dataset.suit = String(card.s);
  } else { delete node.dataset.rank; delete node.dataset.suit; }
  node.innerHTML = DECK[faceOf(card)];
  const svg = node.firstElementChild as SVGSVGElement;
  svg.removeAttribute('class'); // Avoid unrelated site-wide .card styles.
  svg.setAttribute('width', '100%');
  svg.setAttribute('height', '100%');
  scopeIds(svg);
}

export function createCardNode(card: Card | null, width: number, height: number): HTMLDivElement {
  const node = document.createElement('div');
  node.className = 'poker-remastered-card';
  node.style.width = `${width}px`;
  node.style.height = `${height}px`;
  node.style.pointerEvents = 'none';
  node.setAttribute('aria-hidden', 'true');
  setCardFace(node, card);
  return node;
}
