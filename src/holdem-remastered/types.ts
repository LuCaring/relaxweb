export interface Card { r: number; s: number }
export interface Player {
  username: string; nickname: string; stack: number; bet: number; hand_bet: number;
  folded: boolean; allin: boolean; in_hand: boolean; dealer: boolean;
}
export interface Options {
  check?: boolean; call?: boolean; call_amount?: number; can_raise?: boolean;
  raise_min: number; raise_max: number; allin?: boolean; allin_to: number;
}
export interface HandRow {
  username: string; nickname?: string; cards?: Card[]; hand_name?: string;
  folded?: boolean; committed?: number;
}
export interface Result {
  pot?: number; board?: Card[]; payouts?: Record<string, number>;
  hands?: HandRow[]; reveal?: HandRow[]; committed?: Record<string, number>;
}
export interface Room {
  room_id: number | string; game_type: string; status: string; spectator?: boolean;
  watching?: string;
  paused?: boolean; hand_no?: number; blind: number; stage?: string; board?: Card[];
  pot?: number; to_act?: string | null; turn_left?: number; last_action?: { nickname: string; text: string };
  players: Player[]; your_hole?: Card[]; your_options?: Options; result?: Result | null;
}
export interface Callbacks {
  send: (payload: PokerAction) => boolean;
  self: () => string;
  coins: (value: number) => string;
  onSeats: () => void;
}

export type PokerAction =
  | { type: 'poker_action'; action: 'fold' | 'check' | 'call' }
  | { type: 'poker_action'; action: 'raise'; raise_to: number };

export const SUITS = ['♠', '♥', '♦', '♣'];
export const RANKS: Record<number, string> = { 11: 'J', 12: 'Q', 13: 'K', 14: 'A' };
