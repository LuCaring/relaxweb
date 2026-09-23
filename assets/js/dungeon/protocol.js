/* 地下城独立协议层：WebSocket 连接、登录续期、request_id 登记与超时重发。
 *
 * 服务端是唯一数据源（见 docs/dungeon-collaboration-handoff.md 第 1、3 节）：
 * 本层只负责把请求送到服务器、把响应按 request_id 兑现给等待者，并转发
 * 服务器主动推送。超时或断线后的重发沿用同一个 request_id 和参数，依赖
 * 服务端回执保证幂等；确实的新操作才会生成新编号。
 */

const AUTH_TOKEN_KEY = "liveAuthToken";

/* 服务端注入的配置（deploy/serve.py）；直接打开文件时用默认端口兜底。 */
const LIVE_CONFIG = window.LIVE_CONFIG || {};
const CHAT_PORT = LIVE_CONFIG.chat_port || 8765;

const REQUEST_TIMEOUT_MS = 8000;
const RECONNECT_DELAY_MS = 2000;

const listeners = new Map();
const pending = new Map();
let socket = null;
let sequence = 0;
let reconnectTimer = 0;
let connected = false;

export const auth = { user: null };

export function on(type, fn) {
  if (!listeners.has(type)) listeners.set(type, new Set());
  listeners.get(type).add(fn);
  return () => listeners.get(type)?.delete(fn);
}

function emit(type, detail) {
  for (const fn of listeners.get(type) || []) {
    try {
      fn(detail);
    } catch (error) {
      console.error("dungeon handler error", error);
    }
  }
}

function requestId(prefix) {
  sequence += 1;
  return `dg-${prefix}-${Date.now().toString(36)}-${sequence.toString(36)}`;
}

function sendRaw(payload) {
  if (socket?.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify(payload));
  return true;
}

/**
 * 发出一条带 request_id 的请求并返回 Promise。
 *
 * - volatile: 轮询类请求（dungeon_sync），断线即弃，不重发。
 * - 写操作超时自动用同一 request_id 重发一次；再超时才报错，错误上保留
 *   request_id，调用方可用 resend() 继续原编号重试。
 */
export function request(type, payload = {}, { timeoutMs = REQUEST_TIMEOUT_MS, volatile = false } = {}) {
  const request_id = requestId(type.replace(/^dungeon_/, ""));
  return new Promise((resolve, reject) => {
    registerPending(request_id, type, payload, { timeoutMs, volatile, resolve, reject, attempts: 0 });
    dispatch(request_id, type, payload);
  });
}

/** 用原编号、原参数重发一次在途请求（起局回复丢失等场景）。 */
export function resend(request_id) {
  const record = pending.get(request_id);
  if (!record) return false;
  dispatch(request_id, record.type, record.payload);
  return true;
}

function registerPending(request_id, type, payload, { timeoutMs, volatile, resolve, reject, attempts }) {
  const timer = volatile ? 0 : window.setTimeout(() => onTimeout(request_id), timeoutMs);
  pending.set(request_id, { type, payload, volatile, resolve, reject, attempts, timer });
}

function dispatch(request_id, type, payload) {
  const sent = sendRaw({ type, request_id, ...payload });
  if (!sent) {
    const record = pending.get(request_id);
    if (record?.volatile) fail(request_id, { code: "disconnected", message: "连接未建立", retryable: true });
  }
}

function onTimeout(request_id) {
  const record = pending.get(request_id);
  if (!record) return;
  if (record.attempts < 1) {
    record.attempts += 1;
    record.timer = window.setTimeout(() => onTimeout(request_id), REQUEST_TIMEOUT_MS);
    dispatch(request_id, record.type, record.payload);
    return;
  }
  fail(request_id, { code: "timeout", message: "请求超时", request_id, retryable: true });
}

function settle(request_id, { error, data } = {}) {
  const record = pending.get(request_id);
  if (!record) return null;
  window.clearTimeout(record.timer);
  pending.delete(request_id);
  if (error) record.reject(Object.assign(new Error(error.message || error.code), error));
  else record.resolve(data);
  return record;
}

function fail(request_id, error) {
  const record = pending.get(request_id);
  settle(request_id, { error });
  // 只对没有等待者的服务器错误推送做全局提示；
  // 有等待者的错误由各自视图经 reportError 统一转述，避免双重提示。
  if (!record && error.code !== "disconnected") emit("request_error", error);
}

/** 断线重连后，把所有非轮询在途请求按原编号补发一遍。 */
function resendPending() {
  for (const [request_id, record] of [...pending]) {
    if (record.volatile) continue;
    dispatch(request_id, record.type, record.payload);
  }
}

export function login(username, password) {
  return new Promise((resolve, reject) => {
    const done = (user) => (user ? resolve(user) : reject(new Error("用户名或密码错误")));
    const offOk = on("login_success", (user) => { offOk(); offFail(); done(user); });
    const offFail = on("auth_error", (message) => { offOk(); offFail(); done(null); });
    sendRaw({ type: "login", username, password });
  });
}

export function logout() {
  sendRaw({ type: "logout", token: localStorage.getItem(AUTH_TOKEN_KEY) });
  localStorage.removeItem(AUTH_TOKEN_KEY);
}

export function isConnected() {
  return connected;
}

/** 向服务器要一份最新 dungeon_state（刷新、版本冲突恢复的统一入口）。 */
export function refreshState() {
  return request("get_dungeon");
}

function connectSocket() {
  clearTimeout(reconnectTimer);
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${protocol}://${location.hostname}:${CHAT_PORT}/?client=dungeon`);
  socket.addEventListener("open", () => {
    connected = true;
    emit("conn", true);
    const token = localStorage.getItem(AUTH_TOKEN_KEY);
    if (token) sendRaw({ type: "resume", token });
    else emit("need_login", null);
  });
  socket.addEventListener("close", () => {
    const wasConnected = connected;
    connected = false;
    emit("conn", false);
    if (wasConnected) emit("request_error", { code: "disconnected", message: "连接断开，正在重连", retryable: true });
    for (const [request_id, record] of [...pending]) {
      if (record.volatile) fail(request_id, { code: "disconnected", message: "连接断开", retryable: true });
    }
    reconnectTimer = window.setTimeout(connectSocket, RECONNECT_DELAY_MS);
  });
  socket.addEventListener("message", ({ data }) => {
    try {
      handleServerMessage(JSON.parse(data));
    } catch (error) {
      console.error("无法解析服务器消息", error);
    }
  });
}

export function connect() {
  connectSocket();
}

export function handleServerMessage(data) {
  switch (data.type) {
    case "login_success":
      localStorage.setItem(AUTH_TOKEN_KEY, data.token);
      auth.user = data;
      emit("login_success", data);
      emit("auth", data);
      request("get_dungeon");
      break;
    case "resume_success":
      auth.user = data;
      emit("auth", data);
      resendPending();
      request("get_dungeon");
      break;
    case "auth_expired":
      localStorage.removeItem(AUTH_TOKEN_KEY);
      auth.user = null;
      emit("auth", null);
      break;
    case "auth_error":
      emit("auth_error", data.message);
      break;
    case "logout_success":
      auth.user = null;
      emit("auth", null);
      break;
    case "dungeon_state":
      emit("state", data);
      if (data.request_id !== null) settle(data.request_id, { data });
      break;
    case "dungeon_result":
      if (data.result?.battle) emit("battle", data.result.battle);
      settle(data.request_id, { data });
      break;
    case "dungeon_events":
      settle(data.request_id, { data });
      break;
    case "dungeon_comparison":
      settle(data.request_id, { data });
      break;
    case "dungeon_error":
      if (data.request_id === null || !pending.has(data.request_id)) emit("request_error", data);
      settle(data.request_id, { error: data });
      break;
    default:
      break;
  }
}
