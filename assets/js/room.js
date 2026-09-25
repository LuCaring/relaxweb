/* Room phase coordinator: waiting, active play and settlement. */

import { alertDialog } from "./dialog.js";
import { renderGameView, send, setRoomMode, state, updateCoinChip } from "./core.js";
import { gameView, onMessage, registerView } from "./registry.js";
import { closeChatOverlay, resetRoomChat, openChatOverlay, refreshRoomChatComposer, usesCompactDesktopChat, usesDesktopChat } from "./room-chat.js";
import { closeHandResultOverlay, renderHandResultOverlay, renderSettlementView } from "./room-settlement.js";
import { renderRoomLobby } from "./room-waiting.js";
import { observeGameRoom, resetGameAudioRoom } from "./game-audio.js";
import { leaveVoice } from "./room-voice.js";

export { closeChatOverlay };

export function renderRoom() {
  setRoomMode();
  if (usesDesktopChat() || usesCompactDesktopChat()) openChatOverlay();
  else document.getElementById("desktopRoomChat")?.remove();
  const room = state.myRoom;
  if (!room) return;
  if (room.status === "playing") {
    if (room.hand_ready) {
      // Keep the table behind the hand result while players confirm the next hand.
      gameView(room.game_type)?.renderTable();
      renderHandResultOverlay();
    } else if (room.settlement) {
      closeHandResultOverlay();
      renderSettlementView();
    } else {
      closeHandResultOverlay();
      gameView(room.game_type)?.renderTable();
    }
  } else if (room.status === "settled") {
    closeHandResultOverlay();
    renderSettlementView();
  } else {
    closeHandResultOverlay();
    renderRoomLobby();
  }
}

onMessage("game_joined", (data) => {
  observeGameRoom(null, data.room, { initial: true });
  state.myRoom = data.room;
  state.currentGameId = state.myRoom.game_type || state.currentGameId || "holdem";
  resetRoomChat();
  document.dispatchEvent(new Event("gamejoined"));
  updateCoinChip();
  send({ type: "get_finance" });
  renderGameView();
  refreshRoomChatComposer();
});

function reportGameError(data) {
  document.dispatchEvent(new CustomEvent("gameactionerror", { detail: { message: data.message } }));
  void alertDialog(data.message);
}
onMessage("error", reportGameError);
onMessage("game_error", reportGameError);

onMessage("game_update", (data) => {
  if (state.myRoom && data.room_id !== state.myRoom.room_id) return;
  observeGameRoom(state.myRoom, data);
  state.myRoom = data;
  renderGameView();
  refreshRoomChatComposer();
});

onMessage("hand_result", () => {});
onMessage("game_restart", () => {});

onMessage("room_closed", (data) => {
  const hadRoom = Boolean(state.myRoom);
  // 登录恢复时 get_room 对未进游戏的用户也会返回 room_closed；
  // 此时可能正在语音聊天室，不能误断开其 LiveKit 连接。
  if (hadRoom) void leaveVoice();
  resetGameAudioRoom();
  state.myRoom = null;
  resetRoomChat();
  closeChatOverlay();
  closeHandResultOverlay();
  send({ type: "get_finance" });
  if (hadRoom && data.reason) void alertDialog(data.reason);
  renderGameView();
});

registerView("room", renderRoom);
