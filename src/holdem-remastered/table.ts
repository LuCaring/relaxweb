import Phaser from 'phaser';
import type { Card, Room, Callbacks, PokerAction } from './types';
import { SUITS, RANKS } from './types';
import { TableScene } from './scene';
import { seatPoint } from './layout';
import { createCardNode } from './card-svg';

const STAGES: Record<string, string> = { preflop: '翻牌前', flop: '翻牌', turn: '转牌', river: '河牌', showdown: '摊牌' };
const el = (tag: string, className: string, content?: string): HTMLElement => {
  const node = document.createElement(tag);
  node.className = className;
  if (content !== undefined) node.textContent = content;
  return node;
};

export class HoldemTable {
  private root: HTMLElement;
  private stage: HTMLElement;
  private seats: HTMLElement;
  private meta: HTMLElement;
  private status: HTMLElement;
  private actions: HTMLElement;
  private hand: HTMLElement;
  private handTitle: HTMLElement;
  private handCards: HTMLElement;
  private turn: HTMLElement;
  private timer: number | null = null;
  private game: Phaser.Game;
  private scene: TableScene;
  private callbacks: Callbacks;
  private pending = false;
  private lastTurn = '';
  private lastRoom: Room | null = null;
  private deadline = 0;
  private actionsKey = '';
  private handKey = '';
  private handNo = -1;
  private disconnected = false;

  constructor(root: HTMLElement, callbacks: Callbacks) {
    this.root = root; this.callbacks = callbacks;
    this.stage = el('div', 'poker-remastered-stage');
    this.seats = el('div', 'poker-remastered-seats');
    this.meta = el('div', 'poker-remastered-meta');
    this.status = el('div', 'poker-remastered-status');
    this.actions = el('div', 'poker-remastered-actions');
    this.hand = el('section', 'poker-remastered-hand');
    this.handTitle = el('div', 'poker-remastered-hand-title');
    this.handCards = el('div', 'poker-remastered-hand-cards');
    this.hand.setAttribute('role', 'group');
    this.hand.append(this.handTitle, this.handCards);
    this.turn = el('div', 'poker-remastered-turn');
    const stageWrap = el('div', 'poker-remastered-stage-wrap');
    stageWrap.append(this.stage, this.seats);
    const footer = el('section', 'poker-remastered-dock');
    const controls = el('div', 'poker-remastered-controls');
    controls.append(this.meta, this.turn, this.status, this.actions);
    footer.append(this.hand, controls);
    root.append(stageWrap, footer);
    this.scene = new TableScene();
    this.stage.setAttribute('role', 'img');
    this.game = new Phaser.Game({
      type: Phaser.AUTO, parent: this.stage,
      width: Math.max(1, this.stage.clientWidth), height: Math.max(1, this.stage.clientHeight),
      transparent: true,
      scale: { mode: Phaser.Scale.RESIZE },
      dom: { createContainer: true, pointerEvents: 'none' },
      scene: this.scene, render: { antialias: true }, audio: { noAudio: true },
    });
    this.timer = window.setInterval(() => this.tick(), 250);
  }

  update(room: Room): void {
    const currentTurn = `${room.hand_no}:${room.stage}:${room.to_act}`;
    if (this.lastTurn !== currentTurn) this.pending = false;
    this.lastTurn = currentTurn;
    if (this.lastRoom !== room) this.deadline = Date.now() + Math.max(0, Number(room.turn_left || 0)) * 1000;
    this.lastRoom = room;
    this.scene.updateRoom(room);
    this.root.dataset.roomId = String(room.room_id);
    const describeCard = (card: Card): string => `${RANKS[card.r] || card.r}${SUITS[card.s] || '?'}`;
    this.stage.setAttribute('aria-label', `公共牌：${(room.board || []).map(describeCard).join('、') || '尚未发牌'}。底池 ${this.callbacks.coins(room.pot || 0)}。`);
    this.root.classList.toggle('is-paused', Boolean(room.paused));
    this.root.classList.toggle('is-spectator', Boolean(room.spectator));
    this.meta.textContent = `第 ${room.hand_no || '—'} 手  ·  ${STAGES[room.stage || ''] || ''}  ·  盲注 ${room.blind}/${room.blind * 2}`;
    this.status.textContent = room.paused ? '牌局已暂停 · 等待房主继续'
      : room.last_action ? `${room.last_action.nickname}  ${room.last_action.text}`
      : room.to_act ? `等待 ${room.players.find(p => p.username === room.to_act)?.nickname || room.to_act} 行动` : '发牌中…';
    this.renderSeats(room);
    const actionKey = JSON.stringify([room.spectator, room.paused, room.to_act, room.hand_no, room.stage, room.your_options]);
    if (actionKey !== this.actionsKey) {
      this.actionsKey = actionKey;
      this.renderActions(room);
    }
    this.renderHand(room);
    this.tick();
  }

  private renderHand(room: Room): void {
    const owner = this.callbacks.self();
    const player = room.players.find(item => item.username === owner);
    const title = room.spectator ? `正在观看 · ${player?.nickname || owner || '玩家'}的手牌` : '我的手牌';
    const cards = room.your_hole || [];
    const key = JSON.stringify([room.hand_no, owner, title, cards]);
    if (key === this.handKey) return;
    const animate = this.handNo >= 0 && this.handNo !== (room.hand_no || 0)
      && !matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.handKey = key;
    this.handNo = room.hand_no || 0;
    this.handTitle.textContent = title;
    this.hand.setAttribute('aria-label', `${title}：${cards.map(card => `${RANKS[card.r] || card.r}${SUITS[card.s] || '?'}`).join('、') || '尚未发牌'}`);
    if (!cards.length) {
      this.handCards.replaceChildren(el('span', 'poker-remastered-hand-empty', '等待发牌…'));
      return;
    }
    const nodes = cards.map((card, index) => {
      const node = createCardNode(card, 90, 126);
      node.dataset.role = 'hole';
      node.dataset.player = owner || '';
      if (animate) {
        node.classList.add('is-arriving');
        node.style.animationDelay = `${index * 90}ms`;
      }
      return node;
    });
    this.handCards.replaceChildren(...nodes);
  }

  private renderSeats(room: Room): void {
    this.seats.replaceChildren();
    room.players.forEach((player, index) => {
      const point = seatPoint(index, room.players.length);
      const seat = el('div', 'poker-remastered-seat');
      seat.dataset.seat = String(index);
      seat.dataset.username = player.username;
      seat.style.left = `${point.x * 100}%`;
      seat.style.top = `${point.y * 100}%`;
      if (player.folded) seat.classList.add('is-folded');
      if (player.username === room.to_act && !room.paused) seat.classList.add('is-active');
      if (player.username === this.callbacks.self()) seat.classList.add('is-me');
      if (room.result?.payouts?.[player.username]) seat.classList.add('is-winner');
      const avatar = el('span', 'poker-remastered-avatar', (player.nickname || player.username).slice(0, 1).toUpperCase());
      const info = el('span', 'poker-remastered-seat-info');
      const name = el('span', 'poker-remastered-seat-name', player.nickname || player.username);
      name.title = player.nickname || player.username;
      const stack = el('span', 'poker-remastered-seat-stack', `◆ ${this.callbacks.coins(player.stack)}`);
      info.append(name, stack);
      if (player.bet > 0) info.append(el('span', 'poker-remastered-seat-bet', `本轮 ${this.callbacks.coins(player.bet)}`));
      if (player.hand_bet > player.bet) info.append(el('span', 'poker-remastered-seat-committed', `本手 ${this.callbacks.coins(player.hand_bet)}`));
      const flag = el('span', 'poker-remastered-seat-flag', player.folded ? '弃牌' : player.allin ? '全下' : !player.in_hand ? '观战' : player.username === room.to_act ? '行动中' : '');
      seat.append(avatar, info, flag);
      if (player.dealer) seat.append(el('span', 'poker-remastered-dealer', 'D'));
      this.seats.append(seat);
    });
    this.callbacks.onSeats();
  }

  private act(action: 'fold' | 'check' | 'call' | 'raise' | 'allin', raiseTo?: number): void {
    if (this.pending || this.disconnected || this.lastRoom?.spectator || this.lastRoom?.paused || Date.now() >= this.deadline) return;
    let payload: PokerAction;
    if (action === 'raise' || action === 'allin') {
      if (raiseTo === undefined) return;
      payload = { type: 'poker_action', action: 'raise', raise_to: raiseTo };
    } else payload = { type: 'poker_action', action };
    if (this.callbacks.send(payload)) {
      this.pending = true;
      this.actions.querySelectorAll('button,input').forEach((item) => { (item as HTMLButtonElement).disabled = true; });
    }
  }

  unlock(): void {
    this.pending = false;
    if (!this.disconnected && Date.now() < this.deadline && !this.lastRoom?.paused && !this.lastRoom?.spectator)
      this.actions.querySelectorAll('button,input').forEach((item) => { (item as HTMLButtonElement).disabled = false; });
  }

  socketClosed(): void {
    this.disconnected = true;
    this.actions.querySelectorAll('button,input').forEach((item) => { (item as HTMLButtonElement).disabled = true; });
    this.turn.textContent = '连接中断 · 正在重连';
  }

  socketRejoined(): void {
    this.disconnected = false;
    this.pending = false;
    this.actionsKey = '';
    this.scene.synchronizeNextSnapshot();
  }

  handResultDelay(): number { return this.scene.handResultDelay(); }

  private button(label: string, action: string, handler: () => void, primary = false): HTMLElement {
    const button = el('button', `poker-remastered-button${primary ? ' is-primary' : ''}`, label);
    button.setAttribute('type', 'button'); button.dataset.action = action;
    button.addEventListener('click', handler);
    this.actions.append(button);
    return button;
  }

  private renderActions(room: Room): void {
    this.actions.replaceChildren();
    const options = room.your_options;
    if (room.spectator) { this.actions.append(el('div', 'poker-remastered-waiting', '观战中 · 可聊天并查看本手结果')); return; }
    if (room.paused) { this.actions.append(el('div', 'poker-remastered-waiting', '牌局暂停中')); return; }
    if (!options || room.to_act !== this.callbacks.self()) {
      const me = room.players.find((p) => p.username === this.callbacks.self());
      this.actions.append(el('div', 'poker-remastered-waiting', me?.folded ? '本手已弃牌 · 等待下一手' : me?.allin ? '已全下 · 等待摊牌' : '等待行动…'));
      return;
    }
    this.button('弃牌', 'fold', () => this.act('fold'));
    if (options.check) this.button('看牌', 'check', () => this.act('check'), true);
    if (options.call) {
      const stack = room.players.find(p => p.username === this.callbacks.self())?.stack || 0;
      this.button(`${(options.call_amount || 0) >= stack ? '全下跟注' : '跟注'} ${this.callbacks.coins(options.call_amount || 0)}`, 'call', () => this.act('call'), true);
    }
    if (options.can_raise) {
      const group = el('div', 'poker-remastered-raise');
      const input = el('input', 'poker-remastered-raise-input') as HTMLInputElement;
      input.type = 'number'; input.inputMode = 'decimal'; input.step = '0.01';
      input.min = String(options.raise_min); input.max = String(options.raise_max);
      input.value = String(options.raise_min); input.setAttribute('aria-label', '加注到的总额');
      const raise = el('button', 'poker-remastered-button is-primary', '加注到');
      raise.setAttribute('type', 'button'); raise.dataset.action = 'raise';
      raise.addEventListener('click', () => {
        const amount = Number(input.value);
        if (!input.value || !Number.isFinite(amount)) { input.reportValidity(); return; }
        this.act('raise', Math.round(Math.min(options.raise_max, Math.max(options.raise_min, amount)) * 100) / 100);
      });
      group.append(input, raise);
      const preset = el('div', 'poker-remastered-presets');
      const ownBet = room.players.find(p => p.username === this.callbacks.self())?.bet || 0;
      const callAmount = options.call_amount || 0;
      for (const [label, amount] of [['最小', options.raise_min], ['半池', ownBet + callAmount + ((room.pot || 0) + callAmount) / 2], ['底池', ownBet + callAmount + (room.pot || 0) + callAmount], ['全下', options.raise_max]] as [string, number][]) {
        const item = el('button', 'poker-remastered-preset', label);
        item.setAttribute('type', 'button');
        item.addEventListener('click', () => { input.value = String(Math.round(Math.max(options.raise_min, Math.min(options.raise_max, amount)) * 100) / 100); input.focus(); });
        preset.append(item);
      }
      group.append(preset); this.actions.append(group);
    }
    if (options.allin) this.button(`全下 ${this.callbacks.coins(options.allin_to)}`, 'allin', () => this.act('allin', options.allin_to));
    if (this.pending) this.actions.querySelectorAll('button,input').forEach((item) => { (item as HTMLButtonElement).disabled = true; });
  }

  private tick(): void {
    const room = this.lastRoom;
    if (!room) return;
    if (this.disconnected) return;
    const active = !room.paused && !room.spectator && room.to_act === this.callbacks.self() && room.your_options;
    if (!active) { this.turn.textContent = ''; this.turn.classList.remove('is-urgent'); return; }
    const seconds = Math.max(0, Math.ceil((this.deadline - Date.now()) / 1000));
    this.turn.textContent = seconds > 0 ? `轮到你了 · 剩余 ${seconds} 秒` : '等待服务器确认…';
    this.turn.classList.toggle('is-urgent', seconds > 0 && seconds <= 5);
    if (!seconds) this.actions.querySelectorAll('button,input').forEach((item) => { (item as HTMLButtonElement).disabled = true; });
  }

  destroy(): void {
    if (this.timer !== null) window.clearInterval(this.timer);
    this.game.destroy(true);
    this.root.remove();
  }
}
