/**
 * 地下城 Beta 原型 · 背景音乐面板。
 *
 * 只负责界面与装配，播放逻辑在 dgn-beta-bgm.js，曲库读写（清单 + IndexedDB）在
 * dgn-beta-bgm-library.js。所有文本都用 textContent 写入：本地曲目的文件名来自用户设备，
 * 不能当 HTML 拼。
 */
import {
  BGM_TRACKS, bgmState, onBgmChange, selectTrack, setBgmAuto, setBgmBufferResolver,
  setBgmSceneSource, setBgmSceneTracks, setBgmVolume,
} from "./dgn-beta-bgm.js";
import {
  addLocalTracks, isLocalLibrarySupported, listBundledTracks,
  listLocalTracks, removeLocalTrack, resolveTrackBytes,
} from "./dgn-beta-bgm-library.js";

const el = (id) => document.getElementById(id);

function trackRow({ id, title, desc, meta, extra, active, onSelect }) {
  const row = document.createElement("div");
  row.className = "dgn-bgm-track" + (active ? " is-active" : "");
  row.dataset.trackId = id;

  const pick = document.createElement("button");
  pick.type = "button";
  pick.className = "dgn-bgm-pick";
  pick.dataset.trackId = id;
  pick.setAttribute("aria-pressed", active ? "true" : "false");
  pick.addEventListener("click", onSelect);

  const head = document.createElement("span");
  head.className = "dgn-bgm-track-head";
  const name = document.createElement("b");
  name.textContent = title;
  head.appendChild(name);
  if (active) {
    const badge = document.createElement("em");
    badge.className = "dgn-bgm-badge";
    badge.textContent = "播放中";
    head.appendChild(badge);
  }
  pick.appendChild(head);

  if (desc) {
    const line = document.createElement("span");
    line.className = "dgn-bgm-track-desc";
    line.textContent = desc;
    pick.appendChild(line);
  }
  if (meta) {
    const line = document.createElement("span");
    line.className = "dgn-bgm-track-meta";
    line.appendChild(meta);
    pick.appendChild(line);
  }
  row.appendChild(pick);

  if (extra) {
    const actions = document.createElement("span");
    actions.className = "dgn-bgm-track-actions";
    actions.appendChild(extra);
    row.appendChild(actions);
  }
  return row;
}

/** 署名行：CC BY 之类的许可要求界面保留作者与许可信息。 */
function creditLine(track) {
  const fragment = document.createDocumentFragment();
  const parts = [track.author, track.license].filter(Boolean);
  fragment.appendChild(document.createTextNode(parts.join(" · ")));
  if (track.licenseUrl) {
    fragment.appendChild(document.createTextNode(" "));
    const link = document.createElement("a");
    link.href = track.licenseUrl;
    link.target = "_blank";
    link.rel = "noreferrer noopener";
    link.textContent = "许可";
    fragment.appendChild(link);
  }
  if (track.source) {
    fragment.appendChild(document.createTextNode(" "));
    const link = document.createElement("a");
    link.href = track.source;
    link.target = "_blank";
    link.rel = "noreferrer noopener";
    link.textContent = "来源";
    fragment.appendChild(link);
  }
  return fragment;
}

export function createBgmPanel({ getWave = () => 1, onStatus = null } = {}) {
  const nodes = {
    panel: el("dgnBgmPanel"),
    open: el("dungeonBgmSettingsButton"),
    close: el("dgnBgmCloseButton"),
    volume: el("dgnBgmVolume"),
    volumeValue: el("dgnBgmVolumeValue"),
    auto: el("dgnBgmAuto"),
    sceneSource: el("dgnBgmSceneSource"),
    tracks: el("dgnBgmTracks"),
    add: el("dgnBgmAddButton"),
    file: el("dgnBgmFileInput"),
    status: el("dgnBgmStatus"),
  };
  let bundled = [];
  let locals = [];
  let opener = null;

  function status(message) {
    if (onStatus) onStatus(message);
    if (nodes.status) nodes.status.textContent = message;
  }

  function renderIndicator(snapshot = bgmState()) {
    if (!nodes.open) return;
    const track = [...BGM_TRACKS, ...bundled, ...locals].find((entry) => entry.id === snapshot.trackId);
    const silent = snapshot.trackId === "off" || !snapshot.playing;
    nodes.open.classList.toggle("dgn-audio-off", silent);
    nodes.open.textContent = "🎵";
    nodes.open.title = snapshot.trackId === "off"
      ? "背景音乐：已关闭"
      : `背景音乐：${track?.title || snapshot.trackId}${snapshot.playing ? "" : "（未播放）"}`;
  }

  function render() {
    const snapshot = bgmState();
    if (nodes.volume) nodes.volume.value = String(snapshot.volume);
    if (nodes.volumeValue) nodes.volumeValue.textContent = `${snapshot.volume}%`;
    if (nodes.auto) nodes.auto.checked = snapshot.auto;
    if (nodes.sceneSource) {
      nodes.sceneSource.value = snapshot.sceneSource;
      nodes.sceneSource.disabled = !snapshot.auto;
    }
    renderIndicator(snapshot);
    if (!nodes.tracks) return;
    nodes.tracks.replaceChildren();

    const pick = (id) => () => {
      // 先写"已选择"，随后引擎若报文件缺失会用更重要的提示覆盖它。
      status(`已选择：${[...BGM_TRACKS, ...bundled, ...locals].find((t) => t.id === id)?.title || id}`);
      selectTrack(id, { auto: false });
      render();
    };

    const synthGroup = document.createElement("div");
    synthGroup.className = "dgn-bgm-group";
    const synthTitle = document.createElement("h4");
    synthTitle.textContent = "内置合成曲（离线可用，无版权限制）";
    synthGroup.appendChild(synthTitle);
    for (const track of BGM_TRACKS) {
      synthGroup.appendChild(trackRow({
        id: track.id,
        title: track.name,
        desc: track.desc,
        active: snapshot.trackId === track.id,
        onSelect: pick(track.id),
      }));
    }
    nodes.tracks.appendChild(synthGroup);

    if (bundled.length) {
      const group = document.createElement("div");
      group.className = "dgn-bgm-group";
      const title = document.createElement("h4");
      title.textContent = `免费曲库（${bundled.length} 首）`;
      group.appendChild(title);
      for (const track of bundled) {
        group.appendChild(trackRow({
          id: track.id,
          title: track.title,
          desc: track.desc,
          meta: creditLine(track),
          active: snapshot.trackId === track.id,
          onSelect: pick(track.id),
        }));
      }
      const note = document.createElement("p");
      note.className = "dgn-bgm-note";
      note.textContent = "音频文件不入库，缺失时选它会提示。安装方法见 assets/dungeon/beta/bgm/README.md。";
      group.appendChild(note);
      nodes.tracks.appendChild(group);
    }

    const localGroup = document.createElement("div");
    localGroup.className = "dgn-bgm-group";
    const localTitle = document.createElement("h4");
    localTitle.textContent = `我的本地曲目（${locals.length} 首，只存在本机浏览器）`;
    localGroup.appendChild(localTitle);
    if (!locals.length) {
      const empty = document.createElement("p");
      empty.className = "dgn-bgm-note";
      empty.textContent = isLocalLibrarySupported()
        ? "还没有添加曲目。点下方按钮选你机器上的音频文件，刷新后仍然保留。"
        : "当前浏览器不支持 IndexedDB，无法保存本地曲目。";
      localGroup.appendChild(empty);
    }
    for (const track of locals) {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "dgn-bgm-remove";
      remove.dataset.trackId = track.id;
      remove.textContent = "删除";
      remove.addEventListener("click", async (event) => {
        event.stopPropagation();
        await removeLocalTrack(track.id);
        locals = locals.filter((entry) => entry.id !== track.id);
        if (bgmState().trackId === track.id) selectTrack("ruins-hall", { auto: false });
        status(`已删除本地曲目：${track.name}`);
        render();
      });
      const size = document.createElement("span");
      size.textContent = `${track.name} · ${(track.size / 1024 / 1024).toFixed(1)}MB`;
      localGroup.appendChild(trackRow({
        id: track.id,
        title: track.name,
        desc: "来自本机文件，不会上传或提交",
        meta: size,
        extra: remove,
        active: snapshot.trackId === track.id,
        onSelect: pick(track.id),
      }));
    }
    nodes.tracks.appendChild(localGroup);
  }

  function open(button) {
    opener = button || nodes.open;
    if (nodes.panel) nodes.panel.hidden = false;
    nodes.close?.focus();
    render();
  }

  function close() {
    if (!nodes.panel || nodes.panel.hidden) return;
    nodes.panel.hidden = true;
    opener?.focus?.();
    opener = null;
  }

  async function refreshLibrary() {
    bundled = await listBundledTracks();
    setBgmSceneTracks(bundled);
    if (isLocalLibrarySupported()) {
      try { locals = await listLocalTracks(); } catch { locals = []; }
    }
    render();
  }

  // 本地曲库交给引擎按需解码；引擎只认原始字节。
  setBgmBufferResolver(resolveTrackBytes);
  onBgmChange((snapshot) => {
    renderIndicator(snapshot);
    if (snapshot.missing) {
      status(`曲目文件缺失：${snapshot.missing}。把文件放进 assets/dungeon/beta/bgm/ 即可，见该目录 README.md`);
    }
  });

  nodes.open?.addEventListener("click", () => open(nodes.open));
  nodes.close?.addEventListener("click", close);
  nodes.panel?.addEventListener("click", (event) => {
    if (event.target === nodes.panel) close();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !nodes.panel || nodes.panel.hidden) return;
    event.preventDefault();
    close();
  });
  nodes.volume?.addEventListener("input", () => setBgmVolume(nodes.volume.value));
  nodes.auto?.addEventListener("change", () => {
    setBgmAuto(nodes.auto.checked, getWave());
    status(nodes.auto.checked ? "已开启跟随场景自动切换" : "已锁定当前曲目");
    render();
  });
  nodes.sceneSource?.addEventListener("change", () => {
    setBgmSceneSource(nodes.sceneSource.value, getWave());
    status(nodes.sceneSource.value === "library" ? "自动切换使用免费曲库" : "自动切换使用内置合成曲");
    render();
  });
  nodes.add?.addEventListener("click", () => nodes.file?.click());
  nodes.file?.addEventListener("change", async () => {
    const files = [...(nodes.file.files || [])];
    nodes.file.value = "";
    if (!files.length) return;
    try {
      const { added, skipped } = await addLocalTracks(files);
      locals = await listLocalTracks();
      const parts = [];
      if (added.length) parts.push(`已添加 ${added.length} 首`);
      if (skipped.length) parts.push(`跳过 ${skipped.length} 首（${skipped.map((s) => s.reason).join("；")}）`);
      status(parts.join("，") || "没有可添加的文件");
    } catch (error) {
      status(error?.message || "添加失败");
    }
    render();
  });
  if (nodes.add && !isLocalLibrarySupported()) {
    nodes.add.disabled = true;
    nodes.add.title = "当前浏览器不支持 IndexedDB";
  }

  return { open, close, render, refreshLibrary };
}
