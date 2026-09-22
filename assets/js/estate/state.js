"use strict";

export const estateStore = {
  snapshot: null,
  selectedPlot: null,
  pending: new Map(),
  clockOffset: 0,
  listeners: new Set(),
  homeSnapshot: null,
  visit: null,
  players: new Map(),
  onlineUsers: new Set(),
  notifications: [],
};

export function setEstateSnapshot(data) {
  estateStore.snapshot = data;
  estateStore.homeSnapshot = data;
  estateStore.visit = null;
  estateStore.clockOffset = Number(data.server_time || 0) * 1000 - Date.now();
  estateStore.listeners.forEach((listener) => listener(data));
}

export function setVisitSnapshot(data) {
  estateStore.visit = data;
  estateStore.snapshot = data;
  estateStore.players = new Map((data.players || []).map((player) => [player.username, player]));
  estateStore.clockOffset = Number(data.server_time || 0) * 1000 - Date.now();
  estateStore.listeners.forEach((listener) => listener(data));
}

export function clearEstate() {
  estateStore.snapshot = null;
  estateStore.selectedPlot = null;
  estateStore.pending.clear();
  estateStore.homeSnapshot = null;
  estateStore.visit = null;
  estateStore.players.clear();
  estateStore.onlineUsers.clear();
  estateStore.notifications = [];
  estateStore.listeners.forEach((listener) => listener(null));
}

export function estateNow() {
  return Math.floor((Date.now() + estateStore.clockOffset) / 1000);
}

export function subscribeEstate(listener) {
  estateStore.listeners.add(listener);
  return () => estateStore.listeners.delete(listener);
}
