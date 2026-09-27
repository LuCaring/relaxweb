import Phaser from 'phaser';
import type { Card, Room } from './types';
import { createCardNode, setCardFace } from './card-svg';
import { chipStackArtwork, setChipStack, sparkArtwork, tableArtwork, transactionArtwork, turnRingArtwork } from './table-svg';
import { boardPoint, chipZonePoint, logoPoint, potLabelPoint, seatChipPoint, seatPoint } from './layout';

const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
const faceKey = (card: Card | null) => card ? `${card.r}:${card.s}` : '';

interface BoardSlot {
  item: Phaser.GameObjects.DOMElement;
  shown: string;
  pending: string;
}

function label(className: string, text: string): HTMLDivElement {
  const node = document.createElement('div');
  node.className = className;
  node.textContent = text;
  node.style.pointerEvents = 'none';
  node.setAttribute('aria-hidden', 'true');
  return node;
}

export class TableScene extends Phaser.Scene {
  private room: Room | null = null;
  private lastHand = -1;
  private lastResult = false;
  private ready = false;
  private domFactor = 1;
  private visualSize: string | null = null;
  private backdrop!: Phaser.GameObjects.DOMElement;
  private logo!: Phaser.GameObjects.DOMElement;
  private pot!: Phaser.GameObjects.DOMElement;
  private chipStack!: Phaser.GameObjects.DOMElement;
  private turnRing!: Phaser.GameObjects.DOMElement;
  private boardSlots: BoardSlot[] = [];
  private cardEpoch = 0;
  private nextFlipAt = 0;
  private nextDealAt = 0;
  private directNextSnapshot = false;
  private effects = new Set<Phaser.GameObjects.DOMElement>();
  private oldHandBets = new Map<string, number>();
  private payoutFinished = false;
  private payoutEndAt = 0;

  constructor() { super('HoldemTable'); }

  create(): void {
    // The Phaser canvas stays transparent. Every visible table element is
    // native SVG or DOM, with Phaser orchestrating its position and tween.
    this.backdrop = this.add.dom(0, 0, tableArtwork(1200, 650)).setOrigin(0).setDepth(0);
    this.backdrop.pointerEvents = 'none';
    this.logo = this.add.dom(0, 0, label('poker-remastered-table-logo', 'RELAX  ·  POKER')).setOrigin(.5).setDepth(1);
    this.logo.pointerEvents = 'none';
    this.pot = this.add.dom(0, 0, label('poker-remastered-pot', '')).setOrigin(.5).setDepth(3);
    this.pot.pointerEvents = 'none';
    this.chipStack = this.add.dom(0, 0, chipStackArtwork(0, 160, 96)).setOrigin(.5).setDepth(4);
    this.chipStack.pointerEvents = 'none';
    this.turnRing = this.add.dom(0, 0, turnRingArtwork(72)).setOrigin(.5).setDepth(2).setVisible(false);
    this.turnRing.pointerEvents = 'none';
    if (!reduceMotion.matches) {
      this.tweens.add({ targets: this.turnRing, alpha: { from: .55, to: 1 }, duration: 800, yoyo: true, repeat: -1 });
    }
    this.scale.on('resize', this.redraw, this);
    this.events.once(Phaser.Scenes.Events.SHUTDOWN, () => {
      this.scale.off('resize', this.redraw, this);
      this.boardSlots.length = 0;
      this.effects.clear();
    });
    this.ready = true;
    this.redraw();
  }

  private get unit(): number { return this.scale.width / 1200; }

  private redraw(): void {
    if (!this.ready) return;
    const w = this.scale.width, h = this.scale.height;
    const container = this.sys.game.domContainer;
    const stage = container?.parentElement;
    // Phaser RESIZE reads zoomed bounds while the canvas is constrained to the
    // stage's CSS box. Convert each DOM object to that box without scaling the
    // entire layer, which would soften SVG artwork at browser zoom.
    this.domFactor = stage ? stage.clientWidth / w : 1;
    const visualSize = `${stage?.clientWidth ?? w}:${stage?.clientHeight ?? h}`;
    // Phaser 4 can emit resize after page scrolling even when the stage's CSS
    // dimensions did not change. Keep active card flips and payout flights.
    if (this.visualSize === visualSize) return;
    this.visualSize = visualSize;
    this.clearEffects();
    this.payoutEndAt = 0;
    const factor = this.domFactor, u = this.unit * factor;
    const table = this.backdrop.node as HTMLElement;
    table.style.width = `${w * factor}px`;
    table.style.height = `${h * factor}px`;
    this.backdrop.updateSize();
    const logo = this.logo.node as HTMLElement;
    logo.style.fontSize = `${Math.round(23 * u)}px`;
    this.logo.updateSize().setPosition(w * factor * logoPoint.x, h * factor * logoPoint.y);
    const pot = this.pot.node as HTMLElement;
    pot.style.fontSize = `${Math.round(28 * u)}px`;
    this.pot.updateSize().setPosition(w * factor * potLabelPoint.x, h * factor * potLabelPoint.y);
    const pile = this.chipStack.node as HTMLElement;
    pile.style.width = `${160 * u}px`;
    pile.style.height = `${96 * u}px`;
    this.chipStack.updateSize().setPosition(w * factor * chipZonePoint.x, h * factor * chipZonePoint.y);
    const ring = this.turnRing.node as HTMLElement;
    ring.style.width = `${72 * u}px`;
    ring.style.height = `${72 * u}px`;
    this.turnRing.updateSize();
    if (this.room) this.drawState(this.room, true);
  }

  private makeCard(card: Card | null, x: number, y: number, animated: boolean, delay: number): Phaser.GameObjects.DOMElement {
    const factor = this.domFactor, u = this.unit * factor;
    x *= factor; y *= factor;
    // DOMElement lives directly in the Scene. Nesting it in a Phaser
    // Container would break the DOM renderer's transforms.
    const item = this.add.dom(x, y, createCardNode(card, 80 * u, 112 * u)).setOrigin(.5).setDepth(10);
    item.pointerEvents = 'none';
    if (animated && !reduceMotion.matches) {
      item.setPosition(this.scale.width * factor / 2, this.scale.height * factor * .16);
      item.setScale(.65).setAlpha(0).setAngle(-12);
      this.tweens.add({ targets: item, x, y, scaleX: 1, scaleY: 1, alpha: 1, angle: 0,
        duration: 460, delay, ease: 'Cubic.Out' });
    }
    return item;
  }

  private clearCards(): void {
    this.cardEpoch += 1;
    for (const item of this.boardSlots.map(slot => slot.item)) {
      this.tweens.killTweensOf(item);
      item.destroy();
    }
    this.boardSlots = [];
    this.nextFlipAt = 0;
    this.nextDealAt = 0;
  }

  private clearEffects(): void {
    for (const item of this.effects) { this.tweens.killTweensOf(item); item.destroy(); }
    this.effects.clear();
  }

  /** The board slots stay in place while only their SVG faces change. */
  private flipBoardSlot(slot: BoardSlot, card: Card, delay: number): void {
    const item = slot.item, epoch = this.cardEpoch, target = faceKey(card);
    slot.pending = target;
    this.tweens.add({ targets: item, scaleX: 0, duration: 180, delay, ease: 'Sine.In',
      onComplete: () => {
        if (epoch !== this.cardEpoch || !this.boardSlots.includes(slot) || slot.pending !== target) return;
        setCardFace(item.node as HTMLDivElement, card);
        slot.shown = target;
        this.tweens.add({ targets: item, scaleX: 1, duration: 180, ease: 'Sine.Out',
          onComplete: () => { if (epoch === this.cardEpoch) slot.pending = ''; } });
      } });
  }

  /** A reconnect can skip historical flips even when the room has the same hand number. */
  synchronizeNextSnapshot(): void { this.directNextSnapshot = true; }

  handResultDelay(): number { return Math.max(0, this.payoutEndAt - Date.now()); }

  private effect(item: Phaser.GameObjects.DOMElement, target: {
    x: number; y: number; scale: number; alpha: number; duration: number; ease: string;
  }): void {
    item.pointerEvents = 'none';
    this.effects.add(item);
    this.tweens.add({ ...target, targets: item, onComplete: () => {
      this.effects.delete(item);
      item.destroy();
    } });
  }

  private flyChips(room: Room, index: number, amount: number, direction: 'bet' | 'payout', total: number, delay = 0): number {
    const w = this.scale.width * this.domFactor, h = this.scale.height * this.domFactor;
    const seat = seatChipPoint(index, room.players.length);
    const start = direction === 'bet' ? seat : chipZonePoint;
    const end = direction === 'bet' ? chipZonePoint : seat;
    const count = Math.max(1, Math.min(5, Math.ceil(amount / Math.max(total * .06, .01))));
    const duration = 480 + count * 95;
    const item = this.add.dom(w * start.x, h * start.y,
      transactionArtwork(amount, count, direction, 94 * this.unit * this.domFactor))
      .setOrigin(.5).setDepth(20).setAlpha(0);
    item.pointerEvents = 'none';
    (item.node as HTMLElement).dataset.player = room.players[index].username;
    this.effects.add(item);
    this.tweens.add({ targets: item, x: w * end.x, y: h * end.y,
      alpha: { from: 1, to: .18 }, scale: { from: .86, to: 1.08 },
      duration, delay, ease: 'Cubic.InOut',
      onComplete: () => { this.effects.delete(item); item.destroy(); } });
    return duration + delay;
  }

  updateRoom(room: Room): void {
    this.room = room;
    if (this.ready) this.drawState(room, false);
  }

  private drawState(room: Room, resized: boolean): void {
    const w = this.scale.width, h = this.scale.height, factor = this.domFactor;
    const pot = Math.max(0, Number(room.pot || 0));
    (this.pot.node as HTMLElement).textContent = `本手入池  ${pot.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`;
    this.pot.updateSize();
    if (this.lastHand >= 0 && this.lastHand !== (room.hand_no || 0)) this.payoutFinished = false;
    const stackTotal = room.players.reduce((sum, player) => sum + Math.max(0, Number(player.stack || 0)), 0);
    const total = Math.round((stackTotal + (room.result ? 0 : pot)) * 100) / 100;
    const tier = pot > 0 && total > 0 ? Math.max(1, Math.min(5, Math.floor(pot / total * 5) + 1)) : 0;
    const pile = this.chipStack.node as HTMLDivElement;
    if (room.result && (this.lastHand < 0 || resized || this.directNextSnapshot || reduceMotion.matches)) this.payoutFinished = true;
    pile.dataset.amount = String(room.result && this.payoutFinished ? 0 : pot);
    pile.dataset.total = String(total);
    const displayTier = room.result && this.payoutFinished ? 0 : tier;
    if (Number(pile.dataset.tier || 0) !== displayTier) setChipStack(pile, displayTier);
    this.chipStack.setVisible(!room.result || !this.payoutFinished);
    const newHand = this.lastHand >= 0 && this.lastHand !== (room.hand_no || 0);
    const direct = resized || reduceMotion.matches || this.directNextSnapshot;
    if (newHand || direct) {
      this.clearEffects();
      this.payoutEndAt = 0;
    }
    if (this.boardSlots.length !== 5 || newHand || resized) {
      const dealBacks = newHand && !direct && !(room.board?.length);
      this.clearCards();
      for (let i = 0; i < 5; i += 1) {
        const card = room.board?.[i] || null;
        const point = boardPoint(i);
        const item = this.makeCard(card, w * point.x, h * point.y,
          dealBacks, i * 85);
        (item.node as HTMLElement).dataset.role = 'board';
        this.boardSlots.push({ item, shown: faceKey(card), pending: '' });
      }
      if (dealBacks) this.nextDealAt = Date.now() + 460 + 4 * 85;
    }
    if (direct) {
      // A resize, reconnect or reduced-motion update settles every queued flip.
      this.nextFlipAt = 0;
      this.nextDealAt = 0;
      for (let i = 0; i < 5; i += 1) {
        const slot = this.boardSlots[i], card = room.board?.[i] || null;
        this.tweens.killTweensOf(slot.item);
        if (slot.shown !== faceKey(card) || slot.pending) setCardFace(slot.item.node as HTMLDivElement, card);
        slot.shown = faceKey(card);
        slot.pending = '';
        const point = boardPoint(i);
        slot.item.setPosition(w * point.x * factor, h * point.y * factor)
          .setScale(1).setAlpha(1).setAngle(0);
      }
    } else {
      for (let i = 0; i < 5; i += 1) {
        const slot = this.boardSlots[i], card = room.board?.[i] || null;
        const target = faceKey(card);
        if (target === slot.shown || target === slot.pending) continue;
        if (!card) {
          this.tweens.killTweensOf(slot.item);
          setCardFace(slot.item.node as HTMLDivElement, null);
          slot.shown = '';
          slot.pending = '';
          slot.item.setScale(1);
          continue;
        }
        if (slot.pending) {
          this.tweens.killTweensOf(slot.item);
          slot.pending = '';
          slot.item.setScale(1);
        }
        const now = Date.now();
        const start = Math.max(now, this.nextFlipAt, this.nextDealAt);
        this.nextFlipAt = start + 400;
        this.flipBoardSlot(slot, card, start - now);
      }
    }
    this.directNextSnapshot = false;
    if (!room.paused && room.to_act) {
      const index = room.players.findIndex((player) => player.username === room.to_act);
      if (index >= 0) {
        const point = seatPoint(index, room.players.length);
        this.turnRing.setPosition(w * point.x * factor, h * point.y * factor).setVisible(true);
      } else this.turnRing.setVisible(false);
    } else this.turnRing.setVisible(false);
    const sameHand = this.lastHand === (room.hand_no || 0);
    if (!direct && sameHand && !room.result) {
      room.players.forEach((player, index) => {
        const added = Number(player.hand_bet || 0) - (this.oldHandBets.get(player.username) || 0);
        if (added > .001) this.flyChips(room, index, Math.round(added * 100) / 100, 'bet', total);
      });
    }
    if (room.result && !this.lastResult && sameHand && !direct) {
      let lastFlight = 0;
      room.players.forEach((player, index) => {
        const payout = Number(room.result?.payouts?.[player.username] || 0);
        if (payout <= 0) return;
        lastFlight = Math.max(lastFlight, this.flyChips(room, index, payout, 'payout', total, index * 75));
      });
      if (lastFlight) {
        const epoch = this.cardEpoch;
        this.payoutEndAt = Date.now() + lastFlight + 140;
        this.time.delayedCall(lastFlight + 140, () => {
          if (epoch !== this.cardEpoch) return;
          this.payoutFinished = true;
          this.payoutEndAt = 0;
          (this.chipStack.node as HTMLDivElement).dataset.amount = '0';
          setChipStack(this.chipStack.node as HTMLDivElement, 0);
          this.chipStack.setVisible(false);
        });
      } else {
        this.payoutFinished = true;
        this.payoutEndAt = 0;
        (this.chipStack.node as HTMLDivElement).dataset.amount = '0';
        setChipStack(this.chipStack.node as HTMLDivElement, 0);
        this.chipStack.setVisible(false);
      }
      for (let i = 0; i < 28; i += 1) {
        const angle = i * Math.PI * 2 / 28, distance = (40 + i % 4 * 17) * this.unit * factor;
        const spark = this.add.dom(w * factor * chipZonePoint.x, h * factor * chipZonePoint.y,
          sparkArtwork((10 + i % 3 * 3) * this.unit * factor, i % 3 !== 0)).setOrigin(.5).setDepth(21);
        this.effect(spark, { x: w * factor * chipZonePoint.x + Math.cos(angle) * distance * 2,
          y: h * factor * chipZonePoint.y + Math.sin(angle) * distance, alpha: 0, scale: .2,
          duration: 680 + i % 5 * 90, ease: 'Cubic.Out' });
      }
    }
    this.oldHandBets = new Map(room.players.map((player) => [player.username, Number(player.hand_bet || 0)]));
    this.lastHand = room.hand_no || 0;
    this.lastResult = Boolean(room.result);
  }
}
