/** Shared desk coordinates for seats, cards, turn indicator and chip motion. */
export interface Point { x: number; y: number }

const TOP_GAP = Math.PI / 4;

export function seatAngle(index: number, count: number): number {
  if (count <= 1) return 0;
  // Enter beside the dealer on the left, then follow the long arc through
  // the bottom to the dealer's right. Both endpoints mirror each other.
  return Math.PI + TOP_GAP / 2 + index * (Math.PI * 2 - TOP_GAP) / (count - 1);
}

export function seatPoint(index: number, count: number): Point {
  const angle = seatAngle(index, count);
  return { x: .5 + .392 * Math.sin(angle), y: .5 + .39 * Math.cos(angle) };
}

export function seatChipPoint(index: number, count: number): Point {
  const angle = seatAngle(index, count);
  return { x: .5 + .31 * Math.sin(angle), y: .5 + .31 * Math.cos(angle) };
}

export const potLabelPoint: Point = { x: .5, y: .052 };
export const chipZonePoint: Point = { x: .5, y: .302 };
export const logoPoint: Point = { x: .5, y: .61 };
export function boardPoint(index: number): Point { return { x: (392 + index * 104) / 1200, y: .49 }; }
