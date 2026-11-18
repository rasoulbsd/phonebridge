// Phonebridge web UI (API v2, docs/ui-api.md). Plain ES modules: no
// dependencies, no build step. Message text, names and addresses are
// untrusted, so the DOM is only ever built with createElement / textContent.
//
// Modules: lib.js (helpers), api.js (HTTP), render.js (DOM builders),
// this file (state, controllers, socket, dialogs).

import { accessToken, api, mediaUrl, onUnauthorized } from "./api.js";
import { bindDialog, closeDialogQuietly, onBeforeDialogOpen, openDialog } from "./dialogs.js";
import {
  NodeCache,
  TAPBACKS,
  addressKey,
  buildMatcher,
  cleanTypedAddress,
  copyText,
  dayKey,
  dayLabel,
  exactTime,
  fileSize,
  firstName,
  flatten,
  formatAddress,
  h,
  icon,
  listNames,
  listTime,
  looksLikeAddress,
  personName,
  reconcile,
  relativeAgo,
  sameDay,
  snippet,
  storage,
  tapbackGlyph,
  timeFmt,
  toTime,
  truncate,
  validDate,
} from "./lib.js";
import {
  attachmentKind,
  buildChatRow,
  buildEventLine,
  buildHit,
  buildMessage,
  buildTypingRow,
  chatAvatar,
  chatTitle,
  isSmsLike,
  messageSummary,
  personAvatar,
  resolvePerson,
} from "./render.js";
import { AddressPicker } from "./picker.js";

// ------------------------------------------------------------------ settings

// The server fills this in from the app's branding (Phonebridge by default).
const APP_NAME = document.querySelector('meta[name="app-name"]')?.content || "Phonebridge";
const GROUP_WINDOW_MS = 5 * 60 * 1000;
const TIME_GAP_MS = 60 * 60 * 1000;
const NEAR_BOTTOM_PX = 120;
const LOAD_OLDER_PX = 240;
const MAX_JUMP_PAGES = 8;
const COUNTER_AFTER = 140;
const NOTIFY_BODY_MAX = 140;
const NOTIFY_MAX_AGE_MS = 24 * 60 * 60 * 1000;
const RECONNECT_DELAYS_MS = [1000, 2000, 5000, 10000];
const RECONNECT_NOTICE_DELAY_MS = 1500;
const SEARCH_DEBOUNCE_MS = 250;
const HIGHLIGHT_MS = 4000;
const TOAST_MS = 3200;
const LONG_TOAST_MS = 7000;
const WS_POLICY_VIOLATION = 1008;
const TYPING_THROTTLE_MS = 3000;
const TYPING_IDLE_MS = 5000;
const TYPING_HIDE_MS = 15000;
const LONG_PRESS_MS = 450;
// iMessage only allows editing for 15 minutes and unsending for 2 minutes.
const EDIT_WINDOW_MS = 15 * 60 * 1000;
const UNSEND_WINDOW_MS = 2 * 60 * 1000;
const CALL_TIMEOUT_MS = 90 * 1000;
const BLOB_REVOKE_DELAY_MS = 60 * 1000;

const STORAGE_KEYS = {
  theme: "phonebridge.theme",
  lastConversation: "phonebridge.lastConversation",
  notifyAsked: "phonebridge.notifyAsked",
  macHintHidden: "phonebridge.macHintHidden",
};

const mobileQuery = window.matchMedia("(max-width: 760px)");
const reducedMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
const coarsePointerQuery = window.matchMedia("(hover: none) and (pointer: coarse)");

// ----------------------------------------------------------------------- DOM

const dom = {};

function cacheDom() {
  const ids = {
    app: "app",
    bannerMobile: "banner-mobile",
    statusPill: "status-pill",
    statusLabel: "status-label",
    searchInput: "search-input",
    newMessageBtn: "new-message-btn",
    macHint: "mac-hint",
    macHintOpen: "mac-hint-open",
    macHintDismiss: "mac-hint-dismiss",
    notifyCard: "notify-card",
    notifyDismiss: "notify-dismiss",
    notifyEnable: "notify-enable",
    notifyLater: "notify-later",
    listScroll: "list-scroll",
    convSectionLabel: "conv-section-label",
    convList: "conv-list",
    listEmpty: "list-empty",
    searchHits: "search-hits",
    hitsStatus: "hits-status",
    hitList: "hit-list",
    settingsBtn: "settings-btn",
    banner: "banner",
    threadEmpty: "thread-empty",
    emptyText: "empty-text",
    emptyNewBtn: "empty-new-btn",
    thread: "thread",
    backBtn: "back-btn",
    threadAvatar: "thread-avatar",
    threadName: "thread-name",
    threadService: "thread-service",
    threadAddress: "thread-address",
    copyAddressBtn: "copy-address-btn",
    facetimeBtn: "facetime-btn",
    audioCallBtn: "audio-call-btn",
    infoBtn: "info-btn",
    threadScroll: "thread-scroll",
    threadInner: "thread-inner",
    historyEdge: "history-edge",
    threadNotice: "thread-notice",
    messages: "messages",
    newPill: "new-pill",
    composer: "composer",
    composerHint: "composer-hint",
    composerHintText: "composer-hint-text",
    composerContext: "composer-context",
    ccIcon: "cc-icon",
    ccTitle: "cc-title",
    ccSnippet: "cc-snippet",
    ccCancel: "cc-cancel",
    pendingFiles: "pending-files",
    attachBtn: "attach-btn",
    fileInput: "file-input",
    composerInput: "composer-input",
    charCount: "char-count",
    sendBtn: "send-btn",
    dropOverlay: "drop-overlay",
    liveRegion: "live-region",
    callCard: "call-card",
    callAvatar: "call-avatar",
    callName: "call-name",
    callKind: "call-kind",
    callAnswer: "call-answer",
    callDecline: "call-decline",
    connIndicator: "conn-indicator",
    toast: "toast",
    composeDialog: "compose-dialog",
    composeForm: "compose-form",
    toField: "to-field",
    toInput: "to-input",
    toOptions: "to-options",
    composeNote: "compose-note",
    composeText: "compose-text",
    composeHint: "compose-hint",
    composeHintText: "compose-hint-text",
    composeError: "compose-error",
    composeSend: "compose-send",
    settingsDialog: "settings-dialog",
    aboutFacts: "about-facts",
    macSection: "mac-section",
    macFacts: "mac-facts",
    privateApiNote: "private-api-note",
    macForm: "mac-form",
    macUrl: "mac-url",
    macPassword: "mac-password",
    macError: "mac-error",
    macConnect: "mac-connect",
    macDisconnect: "mac-disconnect",
    phoneModeNote: "phone-mode-note",
    phoneLinkRow: "phone-link-row",
    phoneLinkBtn: "phone-link-btn",
    infoDialog: "info-dialog",
    infoAvatar: "info-avatar",
    infoName: "info-name",
    infoSub: "info-sub",
    infoRename: "info-rename",
    renameInput: "rename-input",
    renameSave: "rename-save",
    peopleLabel: "people-label",
    peopleList: "people-list",
    addPerson: "add-person",
    addInput: "add-input",
    addOptions: "add-options",
    leaveBtn: "leave-btn",
    confirmDialog: "confirm-dialog",
    confirmTitle: "confirm-title",
    confirmText: "confirm-text",
    confirmOk: "confirm-ok",
    lightbox: "lightbox",
    lbName: "lb-name",
    lbCount: "lb-count",
    lbDownload: "lb-download",
    lbStage: "lb-stage",
    lbImg: "lb-img",
    lbPrev: "lb-prev",
    lbNext: "lb-next",
    locked: "locked",
  };
  for (const [key, id] of Object.entries(ids)) dom[key] = document.getElementById(id);
}

// --------------------------------------------------------------------- state

const state = {
  status: null,
  chats: [],
  chatsLoaded: false,
  chatsError: null,
  bootstrapped: false,
  openId: null,
  thread: null,
  activeId: null,
  search: { query: "", hits: [], loading: false, error: null, seq: 0, timer: 0 },
  socket: null,
  socketUp: false,
  socketAttempt: 0,
  socketTimer: 0,
  lastFocusSent: null,
  locked: false,
  drafts: new Map(),
  // chatId -> files waiting in the composer: [{key, file, url}]
  attachDrafts: new Map(),
  // temp_id -> optimistic message not yet confirmed by the server; survives switching chats.
  outbox: new Map(),
  sendQueue: Promise.resolve(),
  notified: new Set(),
  // WebSocket updates are numbered so an older HTTP response never overwrites a newer pushed state.
  eventSeq: 0,
  lastEventSeq: new Map(),
  retrying: new Set(),
  readInFlight: new Set(),
  // chatId -> {person, at}: who sent the newest group message, for "Maya: ..." previews.
  lastSenders: new Map(),
  // chatId -> Map(personKey -> {person, timer})
  typingIn: new Map(),
  typingOut: { chatId: null, active: false, lastSent: 0, idleTimer: 0 },
  composerMode: null,
  menu: null,
  suppressClick: false,
  call: null,
  leaving: new Set(),
  closingPhoneLink: false,
  macForm: { busy: false, error: null, dirty: false },
  lightbox: null,
};

const chatCache = new NodeCache();
const messageCache = new NodeCache();

function newThread(id, chat) {
  return {
    id,
    chat,
    messages: [],
    byId: new Map(),
    byTemp: new Map(),
    unsorted: false,
    cursor: null,
    hasMore: false,
    loading: true,
    loadPromise: null,
    olderPromise: null,
    loadingOlder: false,
    olderError: false,
    error: null,
    highlightId: null,
    highlightMatcher: null,
    highlightTimer: 0,
    stick: true,
    smoothUntil: 0,
    focusKey: null,
  };
}

function feature(name) {
  return Boolean(state.status?.features?.[name]);
}

function mode() {
  return state.status?.mode ?? null;
}

function isConnected() {
  return state.status?.state === "connected";
}

function isDesktop() {
  return Boolean(window.pywebview);
}

function findChat(id) {
  return state.chats.find((c) => c.id === id) ?? null;
}

function currentChat() {
  const thread = state.thread;
  if (!thread) return null;
  return findChat(thread.id) ?? thread.chat ?? null;
}

function chatOrStub(id) {
  return findChat(id) ?? (state.thread?.id === id ? state.thread.chat : null) ?? { id, title: "", is_group: false, participants: [], service: null };
}

function isThreadVisible() {
  return Boolean(state.thread) && (!mobileQuery.matches || dom.app.dataset.view === "thread");
}

function uuid() {
  if (typeof crypto.randomUUID === "function") {
    try {
      return crypto.randomUUID();
    } catch {
      // Not a secure context (plain http on the LAN); fall through.
    }
  }
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function msgKey(message) {
  return message.temp_id ? `t:${message.temp_id}` : `m:${message.id}`;
}

function personKey(person) {
  return person ? addressKey(person.address) || person.address || "?" : "_";
}

// -------------------------------------------------------------------- loaders

async function loadStatus() {
  setStatus(await api.status());
}

async function loadChats() {
  try {
    const list = await api.chats();
    state.chats = Array.isArray(list) ? list : [];
    for (const chat of state.chats) rememberChatSender(chat);
    sortChats();
    state.chatsLoaded = true;
    state.chatsError = null;
  } catch (err) {
    if (!state.chatsLoaded) state.chatsError = err.message;
    throw err;
  } finally {
    renderConversationList();
    renderThreadHeader();
    updateTitle();
  }
}

async function bootstrap() {
  state.chatsError = null;
  renderConversationList();
  await Promise.allSettled([loadStatus(), loadChats()]);
  state.bootstrapped = true;
  if (state.locked || state.openId || mobileQuery.matches) return;
  const last = storage.get(STORAGE_KEYS.lastConversation);
  if (last && findChat(last)) openConversation(last);
}

/** Refetch everything a dropped socket might have missed. */
async function resync() {
  const tasks = [loadStatus(), loadChats()];
  const thread = state.thread;
  if (thread) tasks.push(thread.error ? loadThread(thread) : refreshThread(thread));
  await Promise.allSettled(tasks);
}

// ---------------------------------------------------------------------- chats

function sortChats() {
  state.chats.sort((a, b) => toTime(b.last_at) - toTime(a.last_at) || (a.id < b.id ? -1 : 1));
}

/** Chat.last_sender is an optional extension; without it, remember senders from messages we saw. */
function rememberChatSender(chat) {
  if (!chat || !("last_sender" in chat)) return;
  const known = state.lastSenders.get(chat.id);
  if (known && toTime(known.at) === toTime(chat.last_at)) return;
  state.lastSenders.set(chat.id, { person: chat.last_sender, at: chat.last_at, event: false });
}

function rememberMessageSender(message) {
  if (!message || message._local) return;
  const known = state.lastSenders.get(message.chat_id);
  if (known && toTime(known.at) > toTime(message.at)) return;
  state.lastSenders.set(message.chat_id, { person: message.from_me ? null : message.sender, at: message.at, event: Boolean(message.event) });
}

/** What we know about the newest message of a chat, if it matches the chat's last_at. */
function lastKnown(chat) {
  const known = state.lastSenders.get(chat.id);
  return known && toTime(known.at) === toTime(chat.last_at) ? known : null;
}

function lastSenderFor(chat) {
  const known = lastKnown(chat);
  if (!known || known.event || chat.last_from_me) return null;
  return resolvePerson(known.person, chat);
}

function upsertChat(chat) {
  if (!chat?.id) return;
  const index = state.chats.findIndex((c) => c.id === chat.id);
  if (index >= 0) state.chats[index] = chat;
  else state.chats.push(chat);
  rememberChatSender(chat);
  sortChats();
  if (state.thread?.id === chat.id) {
    state.thread.chat = chat;
    renderThreadHeader();
    if (dom.infoDialog.open) renderInfo();
  }
  renderConversationList();
  updateTitle();
  if (state.thread?.id === chat.id && chat.unread > 0) maybeMarkRead();
}

function removeChat(id) {
  state.chats = state.chats.filter((c) => c.id !== id);
  state.drafts.delete(id);
  clearAttachDraft(id);
  state.typingIn.delete(id);
  if (state.thread?.id === id) {
    for (const dialog of [dom.infoDialog, dom.confirmDialog]) if (dialog.open) closeDialogQuietly(dialog);
    closeThread({ focusList: true });
  }
  if (storage.get(STORAGE_KEYS.lastConversation) === id) storage.set(STORAGE_KEYS.lastConversation, null);
  renderConversationList();
  updateTitle();
}

/** Keep the list preview current after our own send, before the server's event arrives. */
function touchChat(message) {
  const chat = findChat(message.chat_id);
  if (!chat || toTime(message.at) < toTime(chat.last_at)) return;
  upsertChat({ ...chat, last_text: messageSummary(message), last_at: message.at, last_from_me: true });
}

function chatMatches(chat, query, digits, numeric) {
  if (chatTitle(chat).toLowerCase().includes(query)) return true;
  for (const person of chat.participants ?? []) {
    const name = (person.name ?? "").toLowerCase();
    const address = (person.address ?? "").toLowerCase();
    if (name.includes(query) || address.includes(query) || formatAddress(person.address).toLowerCase().includes(query)) return true;
    if (numeric && (person.address ?? "").replace(/\D/g, "").includes(digits)) return true;
  }
  return false;
}

function filteredChats() {
  const query = state.search.query.trim().toLowerCase();
  if (!query) return state.chats;
  const digits = query.replace(/\D/g, "");
  const numeric = digits.length >= 3 && /^[\d\s()+.-]+$/.test(query);
  return state.chats.filter((c) => chatMatches(c, query, digits, numeric));
}

function updateTitle() {
  if (state.locked) return;
  const total = state.chats.reduce((sum, c) => sum + Math.max(0, c.unread || 0), 0);
  document.title = total > 0 ? `(${total}) ${APP_NAME}` : APP_NAME;
}

function typingLabel(chatId, { short = false } = {}) {
  const typers = state.typingIn.get(chatId);
  if (!typers?.size) return null;
  const chat = findChat(chatId);
  if (!chat?.is_group || short) return "typing...";
  const names = [...typers.values()].map((t) => (t.person ? firstName(resolvePerson(t.person, chat)) : "Someone"));
  return names.length > 2 ? "Several people are typing..." : `${listNames(names)} ${names.length > 1 ? "are" : "is"} typing...`;
}

function renderConversationList() {
  const list = filteredChats();
  const searching = Boolean(state.search.query.trim());
  const matcher = searching ? buildMatcher(state.search.query) : null;
  const today = new Date().toDateString();

  if (state.activeId && !list.some((c) => c.id === state.activeId)) state.activeId = null;

  chatCache.begin();
  const rows = list.map((chat, index) => {
    const selected = chat.id === state.openId;
    const typing = typingLabel(chat.id);
    const lastSender = chat.is_group ? lastSenderFor(chat) : null;
    const lastIsEvent = Boolean(lastKnown(chat)?.event);
    const signature = JSON.stringify([chat, selected, searching ? state.search.query : "", today, typing, lastSender, lastIsEvent]);
    const row = chatCache.get(chat.id, signature, () => buildChatRow(chat, { selected, matcher, typing, lastSender, lastIsEvent }));
    row.id = `conv-opt-${index}`;
    row.classList.toggle("active", chat.id === state.activeId);
    return row;
  });
  chatCache.prune();
  reconcile(dom.convList, rows);

  const activeIndex = list.findIndex((c) => c.id === state.activeId);
  if (activeIndex >= 0) dom.convList.setAttribute("aria-activedescendant", `conv-opt-${activeIndex}`);
  else dom.convList.removeAttribute("aria-activedescendant");
  dom.convList.tabIndex = list.length ? 0 : -1;
  dom.convSectionLabel.hidden = !searching || list.length === 0;

  renderListEmpty(list.length, searching);
}

function renderListEmpty(count, searching) {
  const el = dom.listEmpty;
  el.className = "list-empty";
  if (count > 0) {
    el.hidden = true;
    el.replaceChildren();
    return;
  }
  el.hidden = false;
  if (!state.chatsLoaded && state.chatsError) {
    el.replaceChildren(
      h("div", { class: "empty-art" }, icon("cloudOff", 30)),
      h("p", { class: "empty-title" }, `Can't reach ${APP_NAME}`),
      h("p", {}, state.chatsError),
      h("button", { type: "button", class: "btn", onclick: () => bootstrap() }, "Try again"),
    );
    return;
  }
  if (!state.chatsLoaded) {
    const skeleton = () => h("div", { class: "skeleton-row", "aria-hidden": "true" }, h("span", { class: "sk-avatar" }), h("div", {}, h("div", { class: "sk-line" }), h("div", { class: "sk-line" })));
    el.className = "list-empty compact";
    el.replaceChildren(h("span", { class: "sr-only" }, "Loading conversations"), skeleton(), skeleton(), skeleton(), skeleton());
    return;
  }
  if (searching) {
    const query = state.search.query.trim();
    el.className = query.length >= 2 ? "list-empty compact" : "list-empty";
    el.replaceChildren(`No conversations match “${query}”.`);
    return;
  }
  if (state.status?.state === "not_configured") {
    el.replaceChildren(
      h("div", { class: "empty-art" }, icon("laptop", 30)),
      h("p", { class: "empty-title" }, "Let's get connected"),
      h("p", {}, "Connect your Mac for full iMessage, or pair your iPhone over Bluetooth."),
      h("button", { type: "button", class: "btn primary", onclick: () => openSettings({ focusMac: true }) }, "Open Settings"),
    );
    return;
  }
  el.replaceChildren(
    h("div", { class: "empty-art" }, icon("message", 30)),
    h("p", { class: "empty-title" }, "No conversations yet"),
    h("p", {}, mode() === "phone" ? "New texts will appear here while your iPhone is connected." : "New messages will appear here."),
    h("button", { type: "button", class: "btn", onclick: () => openCompose() }, "New message"),
  );
}

function setActiveConversation(id, { scroll = false } = {}) {
  state.activeId = id;
  const list = filteredChats();
  const index = list.findIndex((c) => c.id === id);
  for (const row of dom.convList.children) row.classList.toggle("active", row.dataset.id === id);
  if (index >= 0) {
    dom.convList.setAttribute("aria-activedescendant", `conv-opt-${index}`);
    if (scroll) dom.convList.children[index]?.scrollIntoView({ block: "nearest" });
  } else {
    dom.convList.removeAttribute("aria-activedescendant");
  }
}

function onConversationListKeydown(event) {
  dom.convList.classList.add("kbd");
  const list = filteredChats();
  if (!list.length) return;
  let index = list.findIndex((c) => c.id === state.activeId);
  switch (event.key) {
    case "ArrowDown":
      index = Math.min(list.length - 1, index + 1);
      break;
    case "ArrowUp":
      if (index <= 0) {
        event.preventDefault();
        dom.searchInput.focus();
        return;
      }
      index -= 1;
      break;
    case "Home":
      index = 0;
      break;
    case "End":
      index = list.length - 1;
      break;
    case "Enter":
    case " ":
      event.preventDefault();
      if (index >= 0) openConversation(list[index].id, { focusComposer: true });
      return;
    default:
      return;
  }
  event.preventDefault();
  setActiveConversation(list[index].id, { scroll: true });
}

// --------------------------------------------------------------------- search

function onSearchInput() {
  const search = state.search;
  search.query = dom.searchInput.value;
  state.activeId = null;
  renderConversationList();
  clearTimeout(search.timer);
  const query = search.query.trim();
  if (query.length < 2 || !feature("search")) {
    search.seq += 1;
    search.hits = [];
    search.loading = false;
    search.error = null;
    renderSearchHits();
    return;
  }
  search.loading = true;
  renderSearchHits();
  search.timer = setTimeout(() => runSearch(query), SEARCH_DEBOUNCE_MS);
}

async function runSearch(query) {
  const search = state.search;
  const seq = ++search.seq;
  try {
    const hits = await api.search(query);
    if (seq !== search.seq) return;
    search.hits = Array.isArray(hits) ? hits.filter((hit) => hit?.message && hit?.chat) : [];
    search.error = null;
  } catch (err) {
    if (seq !== search.seq) return;
    search.hits = [];
    search.error = err.status === 401 ? null : err.message;
  }
  search.loading = false;
  renderSearchHits();
}

function clearSearch() {
  dom.searchInput.value = "";
  onSearchInput();
}

function renderSearchHits() {
  const { query, hits, loading, error } = state.search;
  const trimmed = query.trim();
  const active = trimmed.length >= 2 && feature("search");
  dom.searchHits.hidden = !active;
  if (!active) {
    dom.hitList.replaceChildren();
    dom.hitsStatus.textContent = "";
    return;
  }
  const matcher = buildMatcher(trimmed);
  dom.hitList.replaceChildren(
    ...hits.map((hit) => {
      const chat = findChat(hit.chat.id) ?? hit.chat;
      return buildHit(hit, chat, matcher, hit.message.text ? snippet(hit.message.text, matcher) : "");
    }),
  );
  if (error) dom.hitsStatus.textContent = error;
  else if (loading && !hits.length) dom.hitsStatus.textContent = "Searching...";
  else if (!loading && !hits.length) dom.hitsStatus.textContent = `No messages match “${trimmed}”.`;
  else dom.hitsStatus.textContent = "";
}

function onHitClick(event) {
  const button = event.target.closest("button.hit");
  if (!button) return;
  const hit = state.search.hits.find((x) => x.message.id === button.dataset.message && x.chat.id === button.dataset.chat);
  if (!hit) return;
  const chat = findChat(hit.chat.id) ?? hit.chat;
  if (!findChat(chat.id)) upsertChat(chat);
  openConversation(chat.id, { chat, focusMessageId: hit.message.id, matcher: buildMatcher(state.search.query) });
}

function focusSearch() {
  if (mobileQuery.matches && dom.app.dataset.view === "thread") {
    if (history.state?.phonebridge === "thread") history.replaceState(null, "");
    closeThread();
  }
  dom.searchInput.focus();
  dom.searchInput.select();
}

// --------------------------------------------------------------------- thread

async function openConversation(rawId, { chat = null, page = null, pageSeq = null, focusMessageId = null, matcher = null, focusComposer = false } = {}) {
  if (state.locked || rawId == null || rawId === "") return;
  const id = String(rawId);
  const same = state.thread?.id === id && !state.thread.error;
  if (!same) {
    saveDraft();
    stopTypingOut();
    closeMenu({ restoreFocus: false });
    state.composerMode = null;
    state.thread = newThread(id, findChat(id) ?? chat ?? page?.chat ?? null);
    messageCache.clear();
    hideNewPill();
  }
  state.openId = id;
  state.activeId = id;
  storage.set(STORAGE_KEYS.lastConversation, id);
  showThreadView();
  renderConversationList();
  if (!same) {
    renderThread();
    loadDraft();
  }
  if (focusComposer && !coarsePointerQuery.matches) dom.composerInput.focus({ preventScroll: true });

  const thread = state.thread;
  if (!same) {
    thread.loadPromise = page ? Promise.resolve().then(() => showFirstPage(thread, page, pageSeq ?? state.eventSeq)) : loadThread(thread);
  }
  await thread.loadPromise;
  if (state.thread !== thread) return;
  if (focusMessageId != null) await jumpToMessage(thread, focusMessageId, matcher);
  maybeMarkRead();
}

async function loadThread(thread) {
  thread.loading = true;
  thread.error = null;
  renderMessages();
  const sinceSeq = state.eventSeq;
  try {
    const page = await api.messages(thread.id);
    if (state.thread !== thread) return;
    showFirstPage(thread, page, sinceSeq);
  } catch (err) {
    if (state.thread !== thread) return;
    thread.loading = false;
    thread.error = err.status === 404 ? "This conversation isn't available anymore." : err.message;
    renderThread();
  }
}

function showFirstPage(thread, page, sinceSeq) {
  for (const message of page?.messages ?? []) applyToThread(thread, message, sinceSeq);
  thread.cursor = page?.cursor ?? null;
  thread.hasMore = Boolean(thread.cursor);
  thread.loading = false;
  if (page?.chat) {
    thread.chat = page.chat;
    upsertChat(page.chat);
  }
  mergeOutbox(thread);
  rememberThreadSender(thread);
  renderThread();
  scrollToBottom(false);
  fillViewport(thread);
}

/** Unconfirmed sends for this chat stay visible after switching away and back. */
function mergeOutbox(thread) {
  for (const local of state.outbox.values()) {
    if (local.chat_id === thread.id && !thread.byTemp.has(local.temp_id)) applyToThread(thread, local);
  }
}

function rememberThreadSender(thread) {
  for (let i = thread.messages.length - 1; i >= 0; i -= 1) {
    const message = thread.messages[i];
    if (!message._local) {
      rememberMessageSender(message);
      return;
    }
  }
}

/** After a reconnect: merge the newest page into what is already on screen. */
async function refreshThread(thread) {
  if (thread.loading) return;
  const sinceSeq = state.eventSeq;
  const page = await api.messages(thread.id);
  if (state.thread !== thread) return;
  const incoming = page.messages ?? [];
  const newest = [...thread.messages].reverse().find((m) => !m._local);
  const oldestIncoming = incoming[0];
  // A full page that starts after everything we have means messages were missed in between.
  const gap = Boolean(page.cursor) && newest && oldestIncoming && !thread.byId.has(oldestIncoming.id) && toTime(oldestIncoming.at) > toTime(newest.at);
  const wasNearBottom = distanceFromBottom() < NEAR_BOTTOM_PX;
  if (gap) {
    thread.messages = [];
    thread.byId = new Map();
    thread.byTemp = new Map();
    thread.cursor = page.cursor;
    thread.hasMore = true;
  }
  let added = false;
  for (const message of incoming) {
    if (applyToThread(thread, message, sinceSeq) === "new") added = true;
  }
  if (gap) mergeOutbox(thread);
  if (page.chat) thread.chat = page.chat;
  renderThread();
  if (added) {
    if (wasNearBottom || gap) scrollToBottom(false);
    else showNewPill();
  }
  maybeMarkRead();
}

/** Sends still in flight stay at the bottom: the server's timestamp may be coarser than the browser's clock. */
function compareMessages(a, b) {
  const pendingA = Boolean(a._local) && a.status === "sending";
  const pendingB = Boolean(b._local) && b.status === "sending";
  if (pendingA !== pendingB) return pendingA ? 1 : -1;
  return toTime(a.at) - toTime(b.at);
}

/** The server's stand-in for a message that is still sending (or failed before reaching the Mac). */
function isPlaceholder(message) {
  return typeof message.id === "string" && message.id.startsWith("temp:");
}

/** Insert or replace a message by id (or by temp_id for our own sends). Returns "new", "updated" or "skipped". */
function applyToThread(thread, message, sinceSeq) {
  if (sinceSeq !== undefined && (state.lastEventSeq.get(message.id) ?? -1) > sinceSeq) return "skipped";
  const existing = thread.byId.get(message.id);
  const local = message.temp_id ? thread.byTemp.get(message.temp_id) : null;
  // A late placeholder (e.g. the POST reply) must not bring "Sending..." back after the real message arrived.
  if (local && isPlaceholder(message) && !local._local && !isPlaceholder(local)) return "skipped";
  // Our own optimistic copy and the server's "temp:" placeholder are both stand-ins: the real message replaces either.
  if (local && local !== existing && local.id !== message.id && !message._local && (local._local || isPlaceholder(local))) {
    // The server confirmed an optimistic send: swap it in place.
    thread.byId.delete(local.id);
    scheduleBlobRevoke(local);
    const index = thread.messages.indexOf(local);
    if (existing) {
      if (index >= 0) thread.messages.splice(index, 1);
    } else if (index >= 0) {
      thread.messages[index] = message;
      thread.byId.set(message.id, message);
      thread.byTemp.set(message.temp_id, message);
      if (local.at !== message.at) thread.unsorted = true;
      return "updated";
    }
  }
  if (message.temp_id) thread.byTemp.set(message.temp_id, message);
  thread.byId.set(message.id, message);
  if (existing) {
    const index = thread.messages.indexOf(existing);
    thread.messages[index] = message;
    if (existing.at !== message.at) thread.unsorted = true;
    return "updated";
  }
  thread.messages.push(message);
  const count = thread.messages.length;
  if (count > 1 && compareMessages(thread.messages[count - 2], message) > 0) thread.unsorted = true;
  return "new";
}

function sortThread(thread) {
  if (!thread.unsorted) return;
  thread.messages.sort(compareMessages);
  thread.unsorted = false;
}

/** Loads the previous page; concurrent callers share the request already in flight. */
function loadOlder(thread) {
  if (thread.olderPromise) return thread.olderPromise;
  if (state.thread !== thread || thread.loading || !thread.hasMore || !thread.cursor) return Promise.resolve(false);
  const promise = fetchOlder(thread).finally(() => {
    if (thread.olderPromise === promise) thread.olderPromise = null;
  });
  thread.olderPromise = promise;
  promise.then((ok) => {
    if (ok) fillViewport(thread);
  });
  return promise;
}

async function fetchOlder(thread) {
  thread.loadingOlder = true;
  thread.olderError = false;
  renderHistoryEdge();
  const sinceSeq = state.eventSeq;
  try {
    const page = await api.messages(thread.id, thread.cursor);
    if (state.thread !== thread) return false;
    const scroller = dom.threadScroll;
    const previousHeight = scroller.scrollHeight;
    const previousTop = scroller.scrollTop;
    const incoming = page.messages ?? [];
    for (const message of incoming) applyToThread(thread, message, sinceSeq);
    thread.cursor = page.cursor ?? null;
    thread.hasMore = Boolean(page.cursor) && incoming.length > 0;
    thread.loadingOlder = false;
    renderMessages();
    // Keep the message the user was looking at in the same place.
    if (!thread.stick) scroller.scrollTop = scroller.scrollHeight - previousHeight + previousTop;
    return true;
  } catch {
    if (state.thread === thread) {
      thread.loadingOlder = false;
      thread.olderError = true;
      renderHistoryEdge();
    }
    return false;
  }
}

/** Short threads don't scroll, so the scroll-to-top trigger would never fire. */
function fillViewport(thread) {
  const scroller = dom.threadScroll;
  if (thread.hasMore && scroller.scrollHeight <= scroller.clientHeight + LOAD_OLDER_PX) loadOlder(thread);
}

function messageRow(messageId) {
  return dom.messages.querySelector(`li[data-id="${CSS.escape(String(messageId))}"]`);
}

async function jumpToMessage(thread, messageId, matcher) {
  let pages = 0;
  while (state.thread === thread && !thread.byId.has(messageId) && thread.hasMore && pages < MAX_JUMP_PAGES) {
    if (!(await loadOlder(thread))) break;
    pages += 1;
  }
  if (state.thread !== thread) return;
  if (!thread.byId.has(messageId)) {
    toast("That message is further back. Scroll up to load older messages.");
    return;
  }
  clearTimeout(thread.highlightTimer);
  thread.highlightId = messageId;
  thread.highlightMatcher = matcher;
  thread.stick = false;
  renderMessages();
  const row = messageRow(messageId);
  if (row) {
    const scroller = dom.threadScroll;
    const offset = row.getBoundingClientRect().top - scroller.getBoundingClientRect().top - (scroller.clientHeight - row.offsetHeight) / 2;
    scroller.scrollTo({ top: scroller.scrollTop + offset, behavior: scrollBehavior(true, offset) });
    if (row.matches("li.msg")) {
      thread.focusKey = row.dataset.key;
      applyRovingFocus();
    }
  }
  thread.highlightTimer = setTimeout(() => {
    if (state.thread !== thread) return;
    thread.highlightId = null;
    thread.highlightMatcher = null;
    renderMessages();
  }, HIGHLIGHT_MS);
}

function closeThread({ focusList = false } = {}) {
  saveDraft();
  stopTypingOut();
  closeMenu({ restoreFocus: false });
  state.composerMode = null;
  state.thread = null;
  state.openId = null;
  messageCache.clear();
  hideNewPill();
  showListView();
  renderThread();
  renderConversationList();
  if (focusList) dom.convList.focus({ preventScroll: true });
}

function showThreadView() {
  if (mobileQuery.matches && dom.app.dataset.view !== "thread" && history.state?.phonebridge !== "thread") {
    // Lets the phone's back gesture return to the list instead of leaving the page.
    history.pushState({ phonebridge: "thread" }, "");
  }
  dom.app.dataset.view = "thread";
  autoGrow(dom.composerInput);
  reportFocus();
}

function showListView() {
  dom.app.dataset.view = "list";
  reportFocus();
}

function onBack() {
  // popstate then closes the thread, keeping browser history and the screen in step.
  if (history.state?.phonebridge === "thread") history.back();
  else closeThread({ focusList: true });
}

function renderThread() {
  const thread = state.thread;
  dom.threadEmpty.hidden = Boolean(thread);
  dom.thread.hidden = !thread;
  if (!thread) return;
  // SMS/RCS chats get green outgoing bubbles, like iMessage.
  dom.thread.dataset.service = isSmsLike(chatOrStub(thread.id)) ? "sms" : "imessage";
  renderThreadHeader();
  renderMessages();
  renderComposerContext();
  renderPendingFiles();
  updateComposer();
}

function renderThreadHeader() {
  const thread = state.thread;
  if (!thread) return;
  const chat = chatOrStub(thread.id);
  dom.threadAvatar.replaceChildren(chatAvatar(chat, "avatar-lg"));
  dom.threadName.textContent = (chat.title || chat.participants?.length) ? chatTitle(chat) : "";
  const people = chat.participants ?? [];
  let sub = "";
  if (chat.is_group) {
    sub = people.length ? `${listNames(people.map((p) => firstName(p)))}` : "Group";
    dom.copyAddressBtn.hidden = true;
  } else {
    const other = people[0];
    sub = other?.name?.trim() ? formatAddress(other.address) : other ? "Not in your contacts" : "";
    dom.copyAddressBtn.hidden = !other?.address;
    const label = other?.address?.includes("@") ? "Copy email address" : "Copy phone number";
    dom.copyAddressBtn.setAttribute("aria-label", label);
    dom.copyAddressBtn.title = label;
  }
  dom.threadAddress.textContent = sub;
  dom.threadAddress.title = sub;
  dom.threadService.hidden = !isSmsLike(chat);
  dom.threadService.textContent = chat.service ?? "";
  const canCall = feature("direct_calls") && !chat.is_group;
  dom.facetimeBtn.hidden = !canCall;
  dom.audioCallBtn.hidden = !canCall;
  dom.infoBtn.setAttribute("aria-label", chat.is_group ? "Group details" : "Conversation details");
}

function renderHistoryEdge() {
  const thread = state.thread;
  const edge = dom.historyEdge;
  if (!thread) return;
  if (thread.loadingOlder || (thread.loading && thread.messages.length === 0)) {
    edge.replaceChildren(h("span", { class: "spinner", "aria-hidden": "true" }), thread.loading ? "Loading messages..." : "Loading older messages...");
  } else if (thread.olderError) {
    edge.replaceChildren("Couldn't load older messages. ", h("button", { type: "button", class: "link-btn", onclick: () => loadOlder(thread) }, "Try again"));
  } else if (!thread.hasMore && thread.messages.length > 0) {
    edge.replaceChildren(mode() === "phone" ? "Start of the history saved on this PC" : "Start of the conversation");
  } else {
    edge.replaceChildren();
  }
}

function renderThreadNotice() {
  const thread = state.thread;
  const notice = dom.threadNotice;
  if (thread?.error) {
    notice.hidden = false;
    notice.replaceChildren(h("p", {}, thread.error), h("button", { type: "button", class: "btn", onclick: () => (thread.loadPromise = loadThread(thread)) }, "Try again"));
  } else if (thread && !thread.loading && thread.messages.length === 0) {
    notice.hidden = false;
    notice.replaceChildren(h("p", {}, "No messages yet."), h("p", {}, "Write the first one below."));
  } else {
    notice.hidden = true;
    notice.replaceChildren();
  }
}

function sameSender(a, b) {
  if (a.from_me !== b.from_me) return false;
  return a.from_me || personKey(a.sender) === personKey(b.sender);
}

function canGroup(a, b) {
  if (a.event || b.event || !sameSender(a, b)) return false;
  const da = validDate(a.at);
  const db = validDate(b.at);
  if (!da || !db || !sameDay(da, db)) return false;
  return db - da <= GROUP_WINDOW_MS;
}

function shortWhen(iso) {
  const date = validDate(iso);
  if (!date) return "";
  const now = new Date();
  if (sameDay(date, now)) return timeFmt.format(date);
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  return sameDay(date, yesterday) ? "Yesterday" : listTime(iso);
}

/** Status line under our own messages: always for sending/failed, otherwise only under the newest one. */
function metaFor(message, isLastOut) {
  if (!message.from_me || message.event || message.unsent) return null;
  if (state.retrying.has(message.id)) return { kind: "sending", text: "Retrying..." };
  if (message.status === "failed") return { kind: "failed" };
  if (message.status === "sending") return { kind: "sending", text: "Sending..." };
  if (!isLastOut) return null;
  if (message.status === "read") {
    return { kind: "read", text: message.read_at ? `Read ${shortWhen(message.read_at)}` : "Read", title: message.read_at ? exactTime(message.read_at) : null };
  }
  if (message.status === "delivered") return { kind: "delivered", text: "Delivered", title: message.delivered_at ? exactTime(message.delivered_at) : null };
  return { kind: "sent", text: "Sent" };
}

/** What can be done with a message right now. */
function messageCaps(message, chat) {
  const live = !message._local && !message.event && !message.unsent && message.status !== "sending" && message.status !== "failed";
  const imessage = !isSmsLike(chat);
  const age = Date.now() - toTime(message.at);
  const downloads = message.unsent || message.event || !feature("attachments") ? [] : (message.attachments ?? []).filter((a) => a.available && mediaUrl(a.url));
  const caps = {
    canReact: live && imessage && feature("reactions"),
    canReply: live && imessage && feature("replies"),
    canEdit: live && imessage && message.from_me && feature("edit") && Boolean(message.text) && age < EDIT_WINDOW_MS,
    canUnsend: live && imessage && message.from_me && feature("unsend") && age < UNSEND_WINDOW_MS,
    canCopy: !message.unsent && !message.event && Boolean(message.text),
    downloads,
  };
  caps.any = caps.canReact || caps.canReply || caps.canEdit || caps.canUnsend || caps.canCopy || downloads.length > 0;
  return caps;
}

function renderMessages() {
  const thread = state.thread;
  if (!thread) return;
  sortThread(thread);
  renderHistoryEdge();
  renderThreadNotice();

  const chat = chatOrStub(thread.id);
  const group = Boolean(chat.is_group);
  const chatSig = group ? JSON.stringify(chat.participants ?? []) : "";
  const featureSig = JSON.stringify(state.status?.features ?? {});
  const messages = thread.messages;
  let lastOutIndex = -1;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].from_me && !messages[i].event && messages[i].status !== "sending" && messages[i].status !== "failed") {
      lastOutIndex = i;
      break;
    }
  }

  messageCache.begin();
  const nodes = [];
  let previousDate = null;
  for (let i = 0; i < messages.length; i += 1) {
    const message = messages[i];
    const date = validDate(message.at) ?? new Date(0);
    if (!previousDate || !sameDay(previousDate, date)) {
      const label = dayLabel(date);
      const time = timeFmt.format(date);
      nodes.push(messageCache.get(`day:${dayKey(date)}`, `${label}|${time}`, () => h("li", { class: "sep" }, h("strong", {}, label), ` ${time}`)));
    } else if (date - previousDate > TIME_GAP_MS) {
      const time = timeFmt.format(date);
      nodes.push(messageCache.get(`gap:${message.id}`, time, () => h("li", { class: "sep" }, time)));
    }
    previousDate = date;

    if (message.event) {
      nodes.push(messageCache.get(`ev:${message.id}`, `${message.event}|${message.at}`, () => buildEventLine(message)));
      continue;
    }

    const joinPrevious = i > 0 && canGroup(messages[i - 1], message);
    const joinNext = i < messages.length - 1 && canGroup(message, messages[i + 1]);
    const position = joinPrevious ? (joinNext ? "middle" : "last") : joinNext ? "first" : "single";
    const incomingGroup = group && !message.from_me;
    const caps = messageCaps(message, chat);
    const ctx = {
      chat,
      position,
      showSender: incomingGroup && !joinPrevious,
      avatar: incomingGroup ? (joinNext ? "space" : "show") : null,
      meta: metaFor(message, i === lastOutIndex),
      highlight: thread.highlightId === message.id ? thread.highlightMatcher ?? true : null,
      replySource: message.reply_to ? thread.byId.get(message.reply_to.id) ?? null : null,
      actions: caps.any && !coarsePointerQuery.matches,
      canReact: caps.canReact,
      canReply: caps.canReply,
      media: feature("attachments"),
      selfKey: msgKey(message),
    };
    const replySig = ctx.replySource ? `${ctx.replySource.from_me}|${ctx.replySource.text}|${personKey(ctx.replySource.sender)}` : "";
    const signature = JSON.stringify([
      message,
      position,
      ctx.showSender,
      ctx.avatar,
      ctx.meta,
      ctx.highlight ? String(ctx.highlight) : "",
      ctx.actions,
      caps.canReact,
      caps.canReply,
      replySig,
      chatSig,
      featureSig,
    ]);
    nodes.push(messageCache.get(ctx.selfKey, signature, () => buildMessage(message, ctx)));
  }

  const typers = state.typingIn.get(thread.id);
  if (typers?.size && !thread.loading) {
    const first = [...typers.values()][0];
    const sig = `${personKey(first.person)}|${group}|${chatSig}`;
    nodes.push(messageCache.get("typing", sig, () => buildTypingRow(first.person, chat, group)));
  }
  messageCache.prune();
  reconcile(dom.messages, nodes);
  applyRovingFocus();
}

/** One message in the list is tabbable; arrow keys move between them. */
function applyRovingFocus() {
  const thread = state.thread;
  if (!thread) return;
  const rows = dom.messages.querySelectorAll("li.msg[data-key]");
  let target = null;
  for (const row of rows) {
    if (row.dataset.key === thread.focusKey) target = row;
  }
  target ??= rows[rows.length - 1] ?? null;
  for (const row of rows) row.tabIndex = row === target ? 0 : -1;
}

function onMessagesKeydown(event) {
  const row = event.target;
  if (!(row instanceof HTMLElement) || !row.matches("li.msg[data-key]")) return;
  const rows = [...dom.messages.querySelectorAll("li.msg[data-key]")];
  let index = rows.indexOf(row);
  switch (event.key) {
    case "ArrowUp":
      index = Math.max(0, index - 1);
      if (index === 0 && state.thread?.hasMore) loadOlder(state.thread);
      break;
    case "ArrowDown":
      index = Math.min(rows.length - 1, index + 1);
      break;
    case "Home":
      index = 0;
      break;
    case "End":
      index = rows.length - 1;
      break;
    case "Enter":
    case " ":
    case "ContextMenu":
      event.preventDefault();
      openMessageMenu(row.dataset.id, row.querySelector(".msg-stack") ?? row, "full");
      return;
    case "F10":
      if (!event.shiftKey) return;
      event.preventDefault();
      openMessageMenu(row.dataset.id, row.querySelector(".msg-stack") ?? row, "full");
      return;
    default:
      return;
  }
  event.preventDefault();
  const next = rows[index];
  if (next) {
    state.thread.focusKey = next.dataset.key;
    applyRovingFocus();
    next.focus();
  }
}

function distanceFromBottom() {
  const scroller = dom.threadScroll;
  return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
}

/** Smooth only for short hops; animating across thousands of pixels is slow and gets interrupted. */
function scrollBehavior(smooth, distance) {
  const short = Math.abs(distance) < dom.threadScroll.clientHeight * 1.5;
  return smooth && short && !reducedMotionQuery.matches ? "smooth" : "auto";
}

function scrollToBottom(smooth) {
  const scroller = dom.threadScroll;
  const behavior = scrollBehavior(smooth, distanceFromBottom());
  if (state.thread) {
    state.thread.stick = true;
    if (behavior === "smooth") state.thread.smoothUntil = Date.now() + 700;
  }
  scroller.scrollTo({ top: scroller.scrollHeight, behavior });
  hideNewPill();
}

function showNewPill() {
  dom.newPill.hidden = false;
}

function hideNewPill() {
  dom.newPill.hidden = true;
}

function onThreadScroll() {
  const thread = state.thread;
  if (!thread) return;
  const near = distanceFromBottom() < NEAR_BOTTOM_PX;
  thread.stick = near || Date.now() < thread.smoothUntil;
  if (dom.threadScroll.scrollTop < LOAD_OLDER_PX && !thread.olderError) loadOlder(thread);
  if (near) hideNewPill();
  if (state.menu && !mobileQuery.matches) closeMenu({ restoreFocus: false });
}

function announce(text) {
  dom.liveRegion.textContent = "";
  setTimeout(() => {
    dom.liveRegion.textContent = text;
  }, 60);
}

async function maybeMarkRead() {
  const thread = state.thread;
  if (!thread || thread.loading || thread.error) return;
  const chat = currentChat();
  if (!chat || !(chat.unread > 0)) return;
  if (document.hidden || !document.hasFocus() || !isThreadVisible()) return;
  if (state.readInFlight.has(thread.id)) return;
  const id = thread.id;
  state.readInFlight.add(id);
  upsertChat({ ...chat, unread: 0 });
  try {
    await api.markRead(id);
  } catch {
    // The server pushes the real unread count, so a failed call fixes itself later.
  } finally {
    state.readInFlight.delete(id);
  }
}

// ------------------------------------------------------- message interactions

function onMessagesClick(event) {
  if (state.suppressClick) {
    state.suppressClick = false;
    event.preventDefault();
    return;
  }
  const target = event.target;
  const retry = target.closest("[data-retry]");
  if (retry) {
    retryMessage(retry.dataset.retry);
    return;
  }
  const row = target.closest("li.msg[data-id]");
  const act = target.closest("[data-act]");
  if (act && row) {
    const id = row.dataset.id;
    if (act.dataset.act === "reply") {
      const message = state.thread?.byId.get(id);
      if (message) startReply(message);
    } else {
      openMessageMenu(id, act.dataset.act === "react" ? row.querySelector(".msg-stack") ?? act : act, act.dataset.act === "react" ? "react" : "full");
    }
    return;
  }
  const jump = target.closest("[data-jump]");
  if (jump && state.thread) {
    jumpToMessage(state.thread, jump.dataset.jump, null);
    return;
  }
  const lightbox = target.closest("[data-lightbox]");
  if (lightbox) {
    openLightbox(lightbox.dataset.lightbox);
    return;
  }
  if (target.closest("a, video, audio, button")) return;
  const bubble = target.closest(".bubble");
  if (!bubble || !row) return;
  // Tap shows the exact time on touch screens, where title tooltips never appear.
  const selection = window.getSelection();
  if (selection && !selection.isCollapsed && bubble.contains(selection.anchorNode)) return;
  row.classList.toggle("show-time");
}

function onMessagesContextMenu(event) {
  const target = event.target;
  if (target.closest("a, video, audio")) return;
  const stack = target.closest(".msg-stack");
  const row = target.closest("li.msg[data-id]");
  if (!stack || !row || row.classList.contains("typing-row")) return;
  const selection = window.getSelection();
  // Let the browser menu handle copying a text selection.
  if (selection && !selection.isCollapsed && stack.contains(selection.anchorNode) && event.pointerType !== "touch") return;
  event.preventDefault();
  if (state.menu) return;
  openMessageMenu(row.dataset.id, stack, "full", coarsePointerQuery.matches ? null : { x: event.clientX, y: event.clientY });
}

let press = null;

function onMessagesPointerDown(event) {
  if (event.pointerType !== "touch") return;
  const stack = event.target.closest(".msg-stack");
  const row = event.target.closest("li.msg[data-id]");
  if (!stack || !row || row.classList.contains("typing-row")) return;
  cancelPress();
  press = {
    x: event.clientX,
    y: event.clientY,
    fired: false,
    timer: setTimeout(() => {
      if (!press) return;
      press.fired = true;
      state.suppressClick = true;
      openMessageMenu(row.dataset.id, stack, "full");
    }, LONG_PRESS_MS),
  };
}

function onMessagesPointerMove(event) {
  if (!press) return;
  if (Math.hypot(event.clientX - press.x, event.clientY - press.y) > 10) cancelPress();
}

function cancelPress() {
  if (!press) return;
  clearTimeout(press.timer);
  const fired = press.fired;
  press = null;
  if (fired) setTimeout(() => (state.suppressClick = false), 400);
}

// ------------------------------------------------------------- message menu

function openMessageMenu(messageId, anchor, kind = "full", point = null) {
  closeMenu({ restoreFocus: false });
  const thread = state.thread;
  const message = thread?.byId.get(messageId);
  if (!thread || !message || message.event) return;
  const chat = chatOrStub(thread.id);
  const caps = messageCaps(message, chat);
  const row = messageRow(messageId);
  const items = [];
  const menu = h("div", { class: "msg-menu", role: "menu", "aria-label": "Message actions" });

  if (caps.canReact) {
    const mine = (message.reactions ?? []).find((r) => r.from_me)?.type ?? null;
    const tapbacks = h("div", { class: "tapback-row", role: "group", "aria-label": "React" });
    for (const tapback of TAPBACKS) {
      const on = mine === tapback.type;
      const button = h(
        "button",
        {
          type: "button",
          class: `tapback${on ? " on" : ""}`,
          role: "menuitemcheckbox",
          "aria-checked": String(on),
          "aria-label": on ? `Remove ${tapback.label}` : tapback.label,
          title: on ? `Remove ${tapback.label}` : tapback.label,
          tabindex: "-1",
        },
        tapbackGlyph(tapback.type),
      );
      button.addEventListener("click", () => {
        closeMenu();
        sendReaction(message.id, tapback.type, on);
      });
      tapbacks.append(button);
      items.push(button);
    }
    menu.append(tapbacks);
  }

  if (kind === "full") {
    const list = h("div", { class: "menu-items" });
    const add = (label, iconName, run, { danger = false, keepFocus = false } = {}) => {
      const button = h("button", { type: "button", role: "menuitem", class: `menu-item${danger ? " danger" : ""}`, tabindex: "-1" }, icon(iconName, 17), h("span", {}, label));
      button.addEventListener("click", () => {
        closeMenu({ restoreFocus: keepFocus });
        run();
      });
      list.append(button);
      items.push(button);
    };
    if (caps.canReply) add("Reply", "reply", () => startReply(message));
    if (caps.canCopy) add("Copy text", "copy", () => copyMessage(message), { keepFocus: true });
    if (caps.canEdit) add("Edit", "edit", () => startEdit(message));
    if (caps.canUnsend) add("Unsend", "unsend", () => unsendMessage(message), { danger: true });
    for (const attachment of caps.downloads) {
      add(caps.downloads.length > 1 ? `Download ${truncate(attachment.name || "file", 28)}` : "Download", "download", () => downloadAttachment(attachment), { keepFocus: true });
    }
    if (list.childElementCount) menu.append(list);
  }

  if (!items.length) {
    toast("Nothing to do with this message.");
    return;
  }

  const sheet = mobileQuery.matches;
  const layer = h("div", { class: `menu-layer${sheet ? " sheet" : ""}` }, menu);
  // Close on click (not pointerdown) so the click that dismisses the menu can't land on what's underneath.
  layer.addEventListener("pointerdown", (event) => {
    if (!menu.contains(event.target)) event.preventDefault();
  });
  layer.addEventListener("click", (event) => {
    if (!menu.contains(event.target)) closeMenu();
  });
  layer.addEventListener("contextmenu", (event) => event.preventDefault());
  menu.addEventListener("keydown", (event) => onMenuKeydown(event, items));
  document.body.append(layer);
  if (!sheet) positionMenu(menu, anchor, point, message.from_me);
  state.menu = { layer, returnTo: row ?? anchor, messageId };
  row?.classList.add("menu-open");
  if (row && thread) thread.focusKey = row.dataset.key;
  items[0].focus({ preventScroll: true });
}

function positionMenu(menu, anchor, point, alignEnd) {
  const margin = 8;
  const width = menu.offsetWidth;
  const height = menu.offsetHeight;
  const vw = document.documentElement.clientWidth;
  const vh = document.documentElement.clientHeight;
  let left;
  let top;
  if (point) {
    left = point.x;
    top = point.y;
    if (left + width > vw - margin) left = point.x - width;
    if (top + height > vh - margin) top = point.y - height;
  } else {
    const rect = (anchor ?? document.body).getBoundingClientRect();
    left = alignEnd ? rect.right - width : rect.left;
    top = rect.top - height - margin;
    if (top < margin) top = rect.bottom + margin;
  }
  left = Math.max(margin, Math.min(left, vw - width - margin));
  top = Math.max(margin, Math.min(top, vh - height - margin));
  menu.style.left = `${Math.round(left)}px`;
  menu.style.top = `${Math.round(top)}px`;
}

function onMenuKeydown(event, items) {
  const index = items.indexOf(document.activeElement);
  let next = -1;
  switch (event.key) {
    case "ArrowDown":
    case "ArrowRight":
      next = (index + 1) % items.length;
      break;
    case "ArrowUp":
    case "ArrowLeft":
      next = index <= 0 ? items.length - 1 : index - 1;
      break;
    case "Home":
      next = 0;
      break;
    case "End":
      next = items.length - 1;
      break;
    case "Escape":
    case "Tab":
      event.preventDefault();
      event.stopPropagation();
      closeMenu();
      return;
    default:
      return;
  }
  event.preventDefault();
  items[next]?.focus();
}

function closeMenu({ restoreFocus = true } = {}) {
  const menu = state.menu;
  if (!menu) return;
  state.menu = null;
  menu.layer.remove();
  dom.messages.querySelector("li.menu-open")?.classList.remove("menu-open");
  if (restoreFocus && menu.returnTo instanceof HTMLElement && menu.returnTo.isConnected) {
    menu.returnTo.focus({ preventScroll: true });
  }
}

// ---------------------------------------------------------- message actions

function patchMessage(messageId, patch) {
  const thread = state.thread;
  const existing = thread?.byId.get(messageId);
  if (!existing) return null;
  const updated = { ...existing, ...patch };
  applyToThread(thread, updated);
  renderMessages();
  return updated;
}

/** Undo an optimistic change, unless the server already sent something newer. */
function revertPatch(messageId, optimistic, original) {
  const thread = state.thread;
  if (!thread || thread.byId.get(messageId) !== optimistic) return;
  applyToThread(thread, original);
  renderMessages();
}

async function sendReaction(messageId, type, remove) {
  const thread = state.thread;
  const original = thread?.byId.get(messageId);
  if (!original) return;
  const others = (original.reactions ?? []).filter((r) => !r.from_me);
  const optimistic = patchMessage(messageId, { reactions: remove ? others : [...others, { type, from_me: true, sender: null }] });
  try {
    await api.react(messageId, thread.id, type, remove);
  } catch (err) {
    revertPatch(messageId, optimistic, original);
    if (err.status !== 401) toast(`Couldn't react: ${err.message}`);
  }
}

async function copyMessage(message) {
  toast((await copyText(message.text)) ? "Copied" : "Couldn't copy. Select the text instead.");
}

function downloadAttachment(attachment) {
  const href = mediaUrl(attachment.url);
  if (!href) return;
  const link = h("a", { href, download: attachment.name || "", hidden: true });
  document.body.append(link);
  link.click();
  link.remove();
}

async function unsendMessage(message) {
  const ok = await confirmDialog({
    title: "Unsend this message?",
    text: "It's removed for everyone in this conversation. People on older software may still see it.",
    ok: "Unsend",
  });
  if (!ok || state.thread?.byId.get(message.id) == null) return;
  const thread = state.thread;
  const original = thread.byId.get(message.id);
  const optimistic = patchMessage(message.id, { unsent: true });
  try {
    await api.unsend(message.id, thread.id);
  } catch (err) {
    revertPatch(message.id, optimistic, original);
    if (err.status !== 401) toast(`Couldn't unsend: ${err.message}`);
  }
}

// ----------------------------------------------------------------- lightbox

function lightboxItems() {
  const items = [];
  for (const message of state.thread?.messages ?? []) {
    if (message.unsent) continue;
    for (const attachment of message.attachments ?? []) {
      if (attachmentKind(attachment) === "image" && attachment.available && feature("attachments") && mediaUrl(attachment.url)) items.push(attachment);
    }
  }
  return items;
}

function openLightbox(attachmentId) {
  const items = lightboxItems();
  const index = items.findIndex((a) => a.id === attachmentId);
  if (index < 0) return;
  state.lightbox = { items, index };
  showLightboxItem();
  openDialog(dom.lightbox, dom.lightbox.querySelector("[data-close]"));
}

function showLightboxItem() {
  const box = state.lightbox;
  if (!box) return;
  const attachment = box.items[box.index];
  const src = mediaUrl(attachment.url);
  dom.lbImg.src = src;
  dom.lbImg.alt = attachment.name || "Photo";
  dom.lbName.textContent = attachment.name || "Photo";
  dom.lbCount.textContent = box.items.length > 1 ? `${box.index + 1} of ${box.items.length}` : "";
  dom.lbDownload.href = src;
  dom.lbDownload.setAttribute("download", attachment.name || "");
  dom.lbPrev.hidden = box.items.length < 2;
  dom.lbNext.hidden = box.items.length < 2;
  dom.lbPrev.disabled = box.index === 0;
  dom.lbNext.disabled = box.index === box.items.length - 1;
}

function stepLightbox(delta) {
  const box = state.lightbox;
  if (!box) return;
  const next = box.index + delta;
  if (next < 0 || next >= box.items.length) return;
  box.index = next;
  showLightboxItem();
}

// ------------------------------------------------------------------ composer

function autoGrow(textarea) {
  if (!textarea.offsetParent) {
    textarea.style.height = "";
    return;
  }
  textarea.style.height = "auto";
  textarea.style.height = `${textarea.scrollHeight}px`;
}

function saveDraft() {
  const thread = state.thread;
  if (!thread) return;
  // While editing, the composer holds the message being edited; the real draft is saved aside.
  const value = state.composerMode?.kind === "edit" ? state.composerMode.savedDraft : dom.composerInput.value;
  if (value.trim()) state.drafts.set(thread.id, value);
  else state.drafts.delete(thread.id);
}

function loadDraft() {
  dom.composerInput.value = state.drafts.get(state.openId) ?? "";
  autoGrow(dom.composerInput);
  updateComposer();
}

function pendingFor(chatId) {
  let list = state.attachDrafts.get(chatId);
  if (!list) {
    list = [];
    state.attachDrafts.set(chatId, list);
  }
  return list;
}

function clearAttachDraft(chatId, { revoke = true } = {}) {
  const list = state.attachDrafts.get(chatId);
  if (revoke) for (const item of list ?? []) URL.revokeObjectURL(item.url);
  state.attachDrafts.delete(chatId);
}

function canAttach() {
  return Boolean(state.thread) && feature("send_attachments") && state.composerMode?.kind !== "edit";
}

function addFiles(files) {
  if (!canAttach()) return;
  const list = pendingFor(state.thread.id);
  for (const file of files) {
    if (!(file instanceof File)) continue;
    list.push({ key: uuid(), file, url: URL.createObjectURL(file) });
  }
  renderPendingFiles();
  updateComposer();
  if (!coarsePointerQuery.matches) dom.composerInput.focus({ preventScroll: true });
}

function removePending(key) {
  const thread = state.thread;
  if (!thread) return;
  const list = pendingFor(thread.id);
  const index = list.findIndex((item) => item.key === key);
  if (index < 0) return;
  URL.revokeObjectURL(list[index].url);
  list.splice(index, 1);
  renderPendingFiles();
  updateComposer();
  dom.composerInput.focus({ preventScroll: true });
}

function renderPendingFiles() {
  const list = state.thread ? state.attachDrafts.get(state.thread.id) ?? [] : [];
  dom.pendingFiles.hidden = list.length === 0;
  dom.pendingFiles.replaceChildren(
    ...list.map((item) => {
      const isImage = item.file.type.startsWith("image/");
      const name = item.file.name || "Attachment";
      return h(
        "li",
        { class: "pending-chip" },
        isImage ? h("img", { class: "pending-thumb", src: item.url, alt: "" }) : h("span", { class: "pending-icon" }, icon("file", 18)),
        h("span", { class: "pending-text" }, h("span", { class: "pending-name", title: name }, name), h("span", { class: "pending-size" }, fileSize(item.file.size))),
        h("button", { type: "button", class: "pending-remove", "aria-label": `Remove ${name}`, title: "Remove", onclick: () => removePending(item.key) }, icon("close", 14)),
      );
    }),
  );
}

function pastedFile(file) {
  if (file.name && file.name !== "image.png") return file;
  const ext = (file.type.split("/")[1] ?? "png").replace("jpeg", "jpg");
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} at ${pad(now.getHours())}.${pad(now.getMinutes())}.${pad(now.getSeconds())}`;
  return new File([file], `Pasted image ${stamp}.${ext}`, { type: file.type });
}

function hasFiles(event) {
  return Array.from(event.dataTransfer?.types ?? []).includes("Files");
}

let dragDepth = 0;

function onDragEnter(event) {
  if (!hasFiles(event) || !canAttach()) return;
  event.preventDefault();
  dragDepth += 1;
  dom.dropOverlay.hidden = false;
}

function onDragOver(event) {
  if (!hasFiles(event)) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = canAttach() ? "copy" : "none";
}

function onDragLeave(event) {
  if (!hasFiles(event)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) dom.dropOverlay.hidden = true;
}

function onDrop(event) {
  if (!hasFiles(event)) return;
  event.preventDefault();
  dragDepth = 0;
  dom.dropOverlay.hidden = true;
  if (canAttach()) addFiles(Array.from(event.dataTransfer.files ?? []));
}

function composerHintText() {
  const status = state.status;
  if (status?.state === "not_configured") return "Connect your Mac or iPhone in Settings to send messages";
  if (status?.state === "auth_failed") return "The Mac rejected the password. Update it in Settings.";
  return status?.mode === "mac" ? "Your Mac isn't connected" : "Your iPhone isn't connected";
}

function updateComposer() {
  const text = dom.composerInput.value;
  const length = Array.from(text).length;
  const editing = state.composerMode?.kind === "edit";
  const pending = state.thread ? state.attachDrafts.get(state.thread.id)?.length ?? 0 : 0;
  dom.charCount.hidden = length <= COUNTER_AFTER;
  dom.charCount.textContent = String(length);
  dom.charCount.title = `${length} characters`;
  const connected = isConnected();
  dom.composerHint.hidden = connected || !state.status;
  dom.composerHintText.textContent = composerHintText();
  if (dom.composerHint.hidden) dom.composerInput.removeAttribute("aria-describedby");
  else dom.composerInput.setAttribute("aria-describedby", "composer-hint");
  dom.attachBtn.hidden = !feature("send_attachments") || editing;
  dom.sendBtn.disabled = !connected || (!text.trim() && (editing || pending === 0));
  dom.sendBtn.classList.toggle("editing", editing);
  dom.sendBtn.setAttribute("aria-label", editing ? "Save edit" : "Send");
  dom.sendBtn.title = editing ? "Save (Enter)" : "Send (Enter)";
  const chat = state.thread ? chatOrStub(state.thread.id) : null;
  dom.composerInput.placeholder = isSmsLike(chat) || mode() === "phone" ? "Text message" : chat?.service === "iMessage" ? "iMessage" : "Message";
  renderCompose();
}

function enterSends(event) {
  return event.key === "Enter" && !event.shiftKey && !event.isComposing && !event.altKey && !coarsePointerQuery.matches;
}

function renderComposerContext() {
  const modeInfo = state.composerMode;
  const thread = state.thread;
  const message = modeInfo && thread?.id === modeInfo.chatId ? thread.byId.get(modeInfo.messageId) : null;
  if (!modeInfo || !message) {
    dom.composerContext.hidden = true;
    return;
  }
  dom.composerContext.hidden = false;
  dom.composerContext.dataset.kind = modeInfo.kind;
  const chat = chatOrStub(thread.id);
  if (modeInfo.kind === "reply") {
    dom.ccIcon.replaceChildren(icon("reply", 16));
    dom.ccTitle.textContent = `Replying to ${message.from_me ? "yourself" : personName(resolvePerson(message.sender, chat))}`;
    dom.ccCancel.setAttribute("aria-label", "Cancel reply");
  } else {
    dom.ccIcon.replaceChildren(icon("edit", 16));
    dom.ccTitle.textContent = "Editing message";
    dom.ccCancel.setAttribute("aria-label", "Cancel editing");
  }
  dom.ccCancel.title = dom.ccCancel.getAttribute("aria-label");
  dom.ccSnippet.textContent = messageSummary(message);
}

function startReply(message) {
  if (!state.thread) return;
  if (state.composerMode?.kind === "edit") cancelComposerMode();
  state.composerMode = { kind: "reply", messageId: message.id, chatId: state.thread.id };
  renderComposerContext();
  updateComposer();
  dom.composerInput.focus({ preventScroll: true });
}

function startEdit(message) {
  if (!state.thread) return;
  const savedDraft = state.composerMode?.kind === "edit" ? state.composerMode.savedDraft : dom.composerInput.value;
  stopTypingOut();
  state.composerMode = { kind: "edit", messageId: message.id, chatId: state.thread.id, savedDraft };
  dom.composerInput.value = message.text;
  autoGrow(dom.composerInput);
  renderComposerContext();
  updateComposer();
  dom.composerInput.focus({ preventScroll: true });
  dom.composerInput.setSelectionRange(dom.composerInput.value.length, dom.composerInput.value.length);
}

function cancelComposerMode() {
  const current = state.composerMode;
  if (!current) return;
  state.composerMode = null;
  if (current.kind === "edit") {
    dom.composerInput.value = current.savedDraft;
    autoGrow(dom.composerInput);
  }
  renderComposerContext();
  updateComposer();
}

async function saveEdit() {
  const current = state.composerMode;
  const thread = state.thread;
  const original = thread?.byId.get(current.messageId);
  const text = dom.composerInput.value.trim();
  cancelComposerMode();
  if (!original || !text || text === original.text) return;
  const optimistic = patchMessage(original.id, { text, edited: true });
  try {
    await api.edit(original.id, thread.id, text);
  } catch (err) {
    revertPatch(original.id, optimistic, original);
    if (err.status !== 401) toast(`Couldn't edit: ${err.message}`);
  }
}

function makeLocal(chatId, { text = "", item = null, replySource = null }) {
  const tempId = uuid();
  const chat = chatOrStub(chatId);
  const file = item?.file ?? null;
  return {
    id: `local:${tempId}`,
    chat_id: chatId,
    temp_id: tempId,
    from_me: true,
    sender: null,
    text,
    at: new Date().toISOString(),
    status: "sending",
    delivered_at: null,
    read_at: null,
    error: null,
    attachments: file
      ? [{ id: `local:${tempId}`, name: file.name || "Attachment", mime: file.type || "application/octet-stream", size: file.size, width: null, height: null, url: item.url, thumb_url: item.url, available: true }]
      : [],
    reactions: [],
    reply_to: replySource
      ? { id: replySource.id, text: messageSummary(replySource), sender_name: replySource.from_me ? null : personName(resolvePerson(replySource.sender, chat)) }
      : null,
    edited: false,
    unsent: false,
    event: null,
    _local: true,
    _file: file,
    _blob: item?.url ?? null,
  };
}

function scheduleBlobRevoke(local) {
  if (local._blob) setTimeout(() => URL.revokeObjectURL(local._blob), BLOB_REVOKE_DELAY_MS);
}

async function sendFromComposer() {
  const thread = state.thread;
  if (!thread) return;
  if (state.composerMode?.kind === "edit") {
    if (isConnected()) saveEdit();
    return;
  }
  const text = dom.composerInput.value.trim();
  const pending = (state.attachDrafts.get(thread.id) ?? []).slice();
  if ((!text && !pending.length) || !isConnected()) return;
  const replyId = state.composerMode?.kind === "reply" ? state.composerMode.messageId : null;
  const replySource = replyId ? thread.byId.get(replyId) ?? null : null;

  dom.composerInput.value = "";
  state.drafts.delete(thread.id);
  clearAttachDraft(thread.id, { revoke: false });
  state.composerMode = null;
  autoGrow(dom.composerInput);
  renderComposerContext();
  renderPendingFiles();
  updateComposer();
  stopTypingOut();

  for (const item of pending) queueSend(makeLocal(thread.id, { item }));
  if (text) queueSend(makeLocal(thread.id, { text, replySource }));
}

function queueSend(local) {
  state.outbox.set(local.temp_id, local);
  showLocal(local, { scroll: true });
  touchChat(local);
  // One request at a time keeps photos and text in the order they were sent.
  state.sendQueue = state.sendQueue.then(() => transmit(local)).catch(() => {});
}

function showLocal(local, { scroll = false } = {}) {
  const thread = state.thread;
  if (!thread || thread.id !== local.chat_id) return;
  const current = thread.byTemp.get(local.temp_id);
  if (current && !current._local) return;
  if (current) {
    const index = thread.messages.indexOf(current);
    thread.byId.delete(current.id);
    if (index >= 0) thread.messages[index] = local;
    thread.byId.set(local.id, local);
    thread.byTemp.set(local.temp_id, local);
  } else {
    applyToThread(thread, local);
  }
  renderMessages();
  if (scroll) scrollToBottom(true);
}

async function transmit(local) {
  // A socket event may already have confirmed it (or it was resent meanwhile).
  if (state.outbox.get(local.temp_id) !== local) return;
  const sinceSeq = state.eventSeq;
  try {
    const message = local._file
      ? await api.upload(local.chat_id, local._file, local.temp_id)
      : await api.send(local.chat_id, { text: local.text, replyTo: local.reply_to?.id ?? null, tempId: local.temp_id });
    if (state.outbox.get(local.temp_id) === local) state.outbox.delete(local.temp_id);
    if (message?.id) applyServerMessage({ ...message, temp_id: message.temp_id ?? local.temp_id }, sinceSeq);
  } catch (err) {
    if (state.outbox.get(local.temp_id) !== local) return;
    const failed = { ...local, status: "failed", error: err.status === 401 ? "Locked" : err.message };
    state.outbox.set(local.temp_id, failed);
    showLocal(failed);
    if (err.status !== 401 && state.openId !== local.chat_id) toast(`Couldn't send to ${chatTitle(chatOrStub(local.chat_id))}: ${err.message}`);
  }
}

/** Show a message returned by POST right away; a later socket update with the same id replaces it. */
function applyServerMessage(message, sinceSeq) {
  const thread = state.thread;
  if (!thread || thread.id !== message.chat_id) return;
  const result = applyToThread(thread, message, sinceSeq);
  if (result === "skipped") return;
  renderMessages();
  if (result === "new") scrollToBottom(true);
}

async function retryMessage(id) {
  const thread = state.thread;
  const message = thread?.byId.get(id);
  if (!message || state.retrying.has(id)) return;
  if (message._local) {
    // It never reached the server; send it again with the same temp_id.
    const again = { ...message, status: "sending", error: null };
    state.outbox.set(again.temp_id, again);
    showLocal(again);
    state.sendQueue = state.sendQueue.then(() => transmit(again)).catch(() => {});
    return;
  }
  state.retrying.add(id);
  renderMessages();
  const sinceSeq = state.eventSeq;
  try {
    const result = await api.retry(id, thread.id);
    state.retrying.delete(id);
    if (result?.id) applyServerMessage(result, sinceSeq);
  } catch (err) {
    if (err.status !== 401) toast(`Couldn't retry: ${err.message}`);
  } finally {
    state.retrying.delete(id);
    renderMessages();
  }
}

// ------------------------------------------------------------------- typing

function onComposerTyping() {
  const thread = state.thread;
  if (!thread || !feature("typing") || !isConnected() || state.composerMode?.kind === "edit") return;
  const typing = state.typingOut;
  if (!dom.composerInput.value.trim()) {
    stopTypingOut();
    return;
  }
  if (typing.chatId !== thread.id) stopTypingOut();
  typing.chatId = thread.id;
  const now = Date.now();
  if (!typing.active || now - typing.lastSent > TYPING_THROTTLE_MS) {
    typing.active = true;
    typing.lastSent = now;
    api.typing(thread.id, true).catch(() => {});
  }
  clearTimeout(typing.idleTimer);
  typing.idleTimer = setTimeout(stopTypingOut, TYPING_IDLE_MS);
}

function stopTypingOut() {
  const typing = state.typingOut;
  clearTimeout(typing.idleTimer);
  if (typing.active && typing.chatId) api.typing(typing.chatId, false).catch(() => {});
  typing.active = false;
  typing.lastSent = 0;
}

function onTypingEvent(chatId, typing, person) {
  if (!chatId) return;
  const key = personKey(person);
  let typers = state.typingIn.get(chatId);
  if (typing) {
    if (!typers) {
      typers = new Map();
      state.typingIn.set(chatId, typers);
    }
    clearTimeout(typers.get(key)?.timer);
    typers.set(key, { person, timer: setTimeout(() => clearTyping(chatId, key), TYPING_HIDE_MS) });
    renderTypingChange(chatId);
  } else {
    clearTyping(chatId, key);
  }
}

function clearTyping(chatId, key = null) {
  const typers = state.typingIn.get(chatId);
  if (!typers) return;
  for (const [k, entry] of typers) {
    if (key === null || k === key) {
      clearTimeout(entry.timer);
      typers.delete(k);
    }
  }
  if (!typers.size) state.typingIn.delete(chatId);
  renderTypingChange(chatId);
}

function renderTypingChange(chatId) {
  renderConversationList();
  if (state.thread?.id === chatId) renderMessages();
}

// ------------------------------------------------------------------- status

function statusLabel(status, { device = true } = {}) {
  switch (status?.state) {
    case "connected":
      return (device && status.device_name) || "Connected";
    case "connecting":
      return "Connecting";
    case "disconnected":
      return "Not connected";
    case "busy":
      return "Busy";
    case "no_phone":
      return "No iPhone";
    case "auth_failed":
      return "Wrong password";
    case "not_configured":
      return "Not set up";
    default:
      return "Connecting";
  }
}

function setStatus(status) {
  if (!status || typeof status !== "object") return;
  const previous = state.status;
  state.status = status;
  if (status.state !== "busy") state.closingPhoneLink = false;
  renderStatus();
  // Different features may change what every row and the composer can show.
  if (JSON.stringify(previous?.features) !== JSON.stringify(status.features) && state.thread) renderThread();
  if (previous && previous.mode !== status.mode && state.bootstrapped) onModeChanged();
  // The list may have loaded while the backend was still connecting (and so empty).
  else if (status.state === "connected" && previous?.state !== "connected" && state.bootstrapped) resync();
}

/** Switching between Mac and iPhone swaps the whole backend, so start the list over. */
function onModeChanged() {
  closeThread();
  state.outbox.clear();
  state.lastSenders.clear();
  state.typingIn.clear();
  state.chatsLoaded = false;
  state.chats = [];
  chatCache.clear();
  renderConversationList();
  loadChats().catch(() => {});
}

function renderStatus() {
  const status = state.status;
  const kind = status?.state ?? "connecting";
  const label = statusLabel(status);
  let tooltip = status?.detail || label;
  if (kind === "connected") {
    if (status.notifications === "push") tooltip = "New messages arrive instantly";
    else if (status.notifications === "polling") tooltip = "Checking for new messages every few seconds";
  }
  dom.statusPill.dataset.state = kind;
  dom.statusLabel.textContent = label;
  dom.statusPill.title = tooltip;
  dom.statusPill.setAttribute("aria-label", `${kind === "connected" ? `Connected to ${label}` : label}. Connection settings`);
  dom.emptyText.textContent =
    mode() === "phone" ? "Choose a conversation on the left, or start a new one. Texts you get while your iPhone is connected show up here." : "Choose a conversation on the left, or start a new one.";
  dom.newMessageBtn.disabled = status?.state === "not_configured";
  dom.emptyNewBtn.disabled = dom.newMessageBtn.disabled;
  renderBanners();
  renderMacHint();
  updateComposer();
  renderSearchHits();
  if (state.thread) renderThreadHeader();
  if (dom.settingsDialog.open) renderSettings();
}

function bannerContent(status) {
  const onMac = status?.mode === "mac";
  switch (status?.state) {
    case "connecting":
      return { tone: "info", spinner: true, title: onMac ? "Connecting to your Mac..." : "Connecting to your iPhone...", text: status.detail };
    case "disconnected":
      return {
        tone: "warn",
        text:
          status.detail ||
          (onMac
            ? "Can't reach your Mac. Make sure it's awake and BlueBubbles Server is running — retrying automatically."
            : "iPhone not reachable. Keep it nearby with Bluetooth on — retrying automatically."),
      };
    case "busy":
      return { tone: "warn", text: status.detail || "Microsoft Phone Link is using the connection.", closePhoneLink: !onMac };
    case "no_phone":
      return { tone: "info", icon: "bluetooth", text: status.detail || "No paired iPhone found. Pair it in Windows Settings > Bluetooth & devices, then keep this app open." };
    case "auth_failed":
      return { tone: "warn", text: "The Mac rejected the password. Update it in Settings.", settings: true };
    case "not_configured":
      return { tone: "info", icon: "laptop", text: status.detail || "iMessage isn't connected yet. Connect your Mac for full iMessage, or pair your iPhone over Bluetooth.", settings: true };
    default:
      return null;
  }
}

function renderBanners() {
  const content = bannerContent(state.status);
  for (const el of [dom.banner, dom.bannerMobile]) {
    if (!content) {
      el.hidden = true;
      el.replaceChildren();
      continue;
    }
    el.hidden = false;
    el.dataset.tone = content.tone;
    const lead = content.spinner ? h("span", { class: "spinner", "aria-hidden": "true" }) : icon(content.icon ?? "alert", 18);
    const text = h("div", { class: "banner-text" });
    if (content.title) text.append(h("span", { class: "banner-title" }, content.title));
    if (content.title && content.text) text.append(" ");
    if (content.text) text.append(h("span", { class: content.title ? "banner-detail" : "" }, content.text));
    el.replaceChildren(lead, text);
    if (content.closePhoneLink) {
      const button = h("button", { type: "button", class: "btn small", onclick: closePhoneLink, disabled: state.closingPhoneLink });
      if (state.closingPhoneLink) button.append(h("span", { class: "spinner", "aria-hidden": "true" }), "Closing...");
      else button.append("Close Phone Link");
      el.append(button);
    }
    if (content.settings) {
      el.append(h("button", { type: "button", class: "btn small", onclick: () => openSettings({ focusMac: true }) }, "Open Settings"));
    }
  }
}

function renderMacHint() {
  const status = state.status;
  const notConfigured = status?.state === "not_configured";
  const phone = status?.mode === "phone" && !storage.get(STORAGE_KEYS.macHintHidden);
  dom.macHint.hidden = !status || !(notConfigured || phone);
  dom.macHintDismiss.hidden = notConfigured;
}

async function closePhoneLink() {
  if (state.closingPhoneLink) return;
  state.closingPhoneLink = true;
  renderBanners();
  if (dom.settingsDialog.open) renderSettings();
  try {
    await api.closePhoneLink();
  } catch (err) {
    state.closingPhoneLink = false;
    renderBanners();
    if (dom.settingsDialog.open) renderSettings();
    if (err.status !== 401) toast(err.message || "Couldn't close Phone Link.");
    return;
  }
  // The status push takes over from here; these are fallbacks if the socket is down.
  for (const delay of [3000, 8000]) {
    setTimeout(() => {
      if (!state.socketUp) loadStatus().catch(() => {});
    }, delay);
  }
  setTimeout(() => {
    if (!state.closingPhoneLink) return;
    state.closingPhoneLink = false;
    renderBanners();
  }, 15000);
}

// ----------------------------------------------------------------- settings

const PRIVATE_API_FEATURES = "reactions, replies, editing and unsending, typing indicators, read receipts, renaming groups and adding or removing people";

function modeName(value) {
  return { mac: "Mac (full iMessage)", phone: "iPhone over Bluetooth", demo: "Demo" }[value] ?? "—";
}

function renderSettings() {
  const status = state.status;
  const how = status?.notifications === "push" ? "Arrive instantly" : status?.notifications === "polling" ? "Checked every few seconds" : "—";
  const lastSync = status?.last_sync_at ? relativeAgo(status.last_sync_at) : "Not yet";
  const facts = [
    ["Using", modeName(status?.mode)],
    [status?.mode === "mac" ? "Mac" : "iPhone", status?.device_name || "Not found yet"],
    ["Status", statusLabel(status, { device: false })],
    ["New messages", how],
    ["Contacts", status ? String(status.contacts_count ?? 0) : "—"],
    ["Last sync", lastSync, status?.last_sync_at ? exactTime(status.last_sync_at) : null],
  ];
  dom.aboutFacts.replaceChildren(...facts.flatMap(([term, value, title]) => [h("dt", {}, term), h("dd", { title }, value)]));

  const mac = status?.mac ?? null;
  const onMac = status?.mode === "mac";
  dom.macFacts.hidden = !mac;
  if (mac) {
    const macFacts = [
      ["Server", mac.url || "—"],
      ["BlueBubbles Server", mac.server_version || "—"],
      ["macOS", mac.macos_version || "—"],
      ["Private API", mac.private_api ? "On" : "Off"],
    ];
    dom.macFacts.replaceChildren(...macFacts.flatMap(([term, value]) => [h("dt", {}, term), h("dd", {}, value)]));
  }
  dom.privateApiNote.hidden = !mac || mac.private_api;
  dom.privateApiNote.textContent = mac && !mac.private_api ? `Turn on the Private API in BlueBubbles Server for ${PRIVATE_API_FEATURES}.` : "";
  if (!state.macForm.dirty && mac?.url && !dom.macUrl.value) dom.macUrl.value = mac.url;

  const form = state.macForm;
  dom.macConnect.disabled = form.busy;
  dom.macConnect.replaceChildren(...(form.busy ? [h("span", { class: "spinner", "aria-hidden": "true" }), "Connecting..."] : [mac ? "Save and reconnect" : "Connect"]));
  dom.macDisconnect.hidden = !onMac;
  dom.macDisconnect.disabled = form.busy;
  dom.macError.hidden = !form.error;
  dom.macError.textContent = form.error ?? "";

  dom.phoneModeNote.textContent = onMac
    ? "Disconnect the Mac to go back to texts from your iPhone over Bluetooth."
    : "Without a Mac, this app reads texts from your iPhone over Bluetooth: one-to-one texts only.";
  const busy = !onMac && status?.state === "busy";
  dom.phoneLinkRow.hidden = !busy;
  dom.phoneLinkBtn.disabled = state.closingPhoneLink;
  dom.phoneLinkBtn.textContent = state.closingPhoneLink ? "Closing..." : "Close Phone Link";
}

async function openSettings({ focusMac = false } = {}) {
  if (state.locked) return;
  state.macForm.error = null;
  renderSettings();
  if (!dom.settingsDialog.open) openDialog(dom.settingsDialog, focusMac ? dom.macUrl : undefined);
  if (focusMac) dom.macSection.scrollIntoView({ block: "start" });
  try {
    const settings = await api.settings();
    if (!state.macForm.dirty && settings?.mac?.url) dom.macUrl.value = settings.mac.url;
  } catch {
    // Settings only prefill the form; status still describes the connection.
  }
}

function normalizeServerUrl(value) {
  const raw = value.trim();
  if (!raw) return "";
  return /^[a-z][a-z\d+.-]*:\/\//i.test(raw) ? raw : `http://${raw}`;
}

async function connectMac() {
  const form = state.macForm;
  if (form.busy) return;
  const url = normalizeServerUrl(dom.macUrl.value);
  const password = dom.macPassword.value;
  if (!url) {
    form.error = "Enter the server address shown in BlueBubbles Server, like http://192.168.1.20:1234.";
    renderSettings();
    dom.macUrl.focus();
    return;
  }
  if (!password) {
    form.error = "Enter the server password you set in BlueBubbles Server.";
    renderSettings();
    dom.macPassword.focus();
    return;
  }
  form.busy = true;
  form.error = null;
  renderSettings();
  try {
    const status = await api.connectMac(url, password);
    dom.macPassword.value = "";
    form.dirty = false;
    dom.macUrl.value = url;
    setStatus(status);
    toast(status?.state === "connected" ? "Connected to your Mac." : "Mac settings saved.");
  } catch (err) {
    form.error = err.status === 401 ? null : err.message;
  } finally {
    form.busy = false;
    renderSettings();
  }
}

async function disconnectMac() {
  const ok = await confirmDialog({
    title: "Disconnect your Mac?",
    text: "The app goes back to texts from your iPhone over Bluetooth. Group chats, photos and reactions won't be available.",
    ok: "Disconnect",
  });
  if (!ok) return;
  const form = state.macForm;
  form.busy = true;
  renderSettings();
  try {
    setStatus(await api.disconnectMac());
    toast("Disconnected from your Mac.");
  } catch (err) {
    if (err.status !== 401) form.error = err.message;
  } finally {
    form.busy = false;
    renderSettings();
  }
}

// ------------------------------------------------------------ notifications

function notificationsSupported() {
  return "Notification" in window && window.isSecureContext && !isDesktop();
}

function maybeShowNotifyPrompt() {
  if (!notificationsSupported() || Notification.permission !== "default") return;
  if (storage.get(STORAGE_KEYS.notifyAsked)) return;
  dom.notifyCard.hidden = false;
}

function dismissNotifyPrompt() {
  storage.set(STORAGE_KEYS.notifyAsked, "1");
  dom.notifyCard.hidden = true;
}

async function enableNotifications() {
  dismissNotifyPrompt();
  try {
    const result = await Notification.requestPermission();
    if (result === "granted") toast("Notifications are on.");
    else if (result === "denied") toast("Notifications are blocked. You can allow them in the browser's site settings.");
  } catch {
    // Very old browsers only offer the callback form; there is nothing useful to do.
  }
}

function canShowWebNotification() {
  return notificationsSupported() && Notification.permission === "granted";
}

function maybeNotify(message, chat, previousUnread) {
  if (message.from_me || message.event || state.notified.has(message.id)) return;
  // Updates (reactions, edits) to old messages arrive as message events too; only new unread ones notify.
  if (previousUnread != null && chat && !(chat.unread > previousUnread)) return;
  state.notified.add(message.id);
  const viewing = state.openId === message.chat_id && !document.hidden && document.hasFocus() && isThreadVisible();
  if (viewing || !canShowWebNotification()) return;
  if (Date.now() - toTime(message.at) > NOTIFY_MAX_AGE_MS) return;
  const source = chat ?? findChat(message.chat_id);
  const sender = resolvePerson(message.sender, source);
  const title = source ? chatTitle(source) : personName(sender);
  const summary = flatten(messageSummary(message)) || "New message";
  const body = truncate(source?.is_group && sender ? `${firstName(sender)}: ${summary}` : summary, NOTIFY_BODY_MAX);
  try {
    const notification = new Notification(title, { body, tag: `phonebridge-${message.chat_id}` });
    notification.addEventListener("click", () => {
      window.focus();
      notification.close();
      openConversation(message.chat_id, { focusComposer: true });
    });
  } catch {
    // Some platforms only allow notifications from a service worker.
  }
}

// -------------------------------------------------------------------- socket

function connectSocket() {
  if (state.locked) return;
  clearTimeout(state.socketTimer);
  const url = new URL("/ws", window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  if (accessToken) url.searchParams.set("token", accessToken);
  let socket;
  try {
    socket = new WebSocket(url);
  } catch {
    scheduleReconnect();
    return;
  }
  state.socket = socket;
  socket.addEventListener("open", () => {
    if (state.socket !== socket) return;
    state.socketUp = true;
    state.socketAttempt = 0;
    state.lastFocusSent = null;
    reportFocus();
    renderConnIndicator();
    if (state.bootstrapped) resync();
  });
  socket.addEventListener("message", (event) => {
    if (state.socket !== socket) return;
    let data;
    try {
      data = JSON.parse(event.data);
    } catch {
      return;
    }
    handleEvent(data);
  });
  socket.addEventListener("close", (event) => {
    if (state.socket !== socket) return;
    state.socket = null;
    state.socketUp = false;
    renderConnIndicator();
    // A policy close means the token was refused; a REST call confirms it and shows the lock screen.
    if (event.code === WS_POLICY_VIOLATION) loadStatus().catch(() => {});
    scheduleReconnect();
  });
}

function scheduleReconnect() {
  if (state.locked) return;
  clearTimeout(state.socketTimer);
  const delay = RECONNECT_DELAYS_MS[Math.min(state.socketAttempt, RECONNECT_DELAYS_MS.length - 1)];
  state.socketAttempt += 1;
  state.socketTimer = setTimeout(connectSocket, delay);
}

/**
 * Tells the desktop app which conversation is in front of the user, so it holds back
 * native notifications only for that one; messages in other chats still pop up.
 */
function reportFocus() {
  const socket = state.socket;
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  const focused = document.hasFocus() && !document.hidden;
  const chatId = focused && isThreadVisible() ? state.openId : null;
  const key = `${focused}|${chatId ?? ""}`;
  if (key === state.lastFocusSent) return;
  state.lastFocusSent = key;
  try {
    socket.send(JSON.stringify({ type: "focus", focused, chat_id: chatId }));
  } catch {
    state.lastFocusSent = null;
  }
}

let connIndicatorTimer = 0;

function renderConnIndicator() {
  clearTimeout(connIndicatorTimer);
  if (state.socketUp || state.locked) {
    dom.connIndicator.hidden = true;
    return;
  }
  // Quick blips reconnect before anyone notices; only show the notice if it lasts.
  connIndicatorTimer = setTimeout(() => {
    dom.connIndicator.hidden = state.socketUp || state.locked;
  }, RECONNECT_NOTICE_DELAY_MS);
}

function handleEvent(event) {
  switch (event?.type) {
    case "status":
      setStatus(event.status);
      break;
    case "chat":
      if (event.chat?.id) upsertChat(event.chat);
      break;
    case "chat_removed":
      if (event.chat_id) onChatRemoved(String(event.chat_id));
      break;
    case "message":
      if (event.message?.id != null) onMessageEvent(event.message, event.chat);
      break;
    case "typing":
      onTypingEvent(event.chat_id, Boolean(event.typing), event.person ?? null);
      break;
    case "call":
      if (event.call?.id) onCallEvent(event.call);
      break;
    default:
      break;
  }
}

function onChatRemoved(id) {
  const chat = findChat(id);
  const left = state.leaving.delete(id);
  removeChat(id);
  if (left) toast(`You left “${chat ? chatTitle(chat) : "the conversation"}”.`);
}

function onMessageEvent(message, chat) {
  state.eventSeq += 1;
  state.lastEventSeq.set(message.id, state.eventSeq);
  if (message.temp_id) state.outbox.delete(message.temp_id);
  const previousUnread = chat?.id ? findChat(chat.id)?.unread ?? null : null;
  rememberMessageSender(message);
  if (!message.from_me) {
    const group = chat?.is_group ?? findChat(message.chat_id)?.is_group;
    if (group) {
      clearTyping(message.chat_id, personKey(message.sender));
      clearTyping(message.chat_id, "_");
    } else {
      clearTyping(message.chat_id);
    }
  }
  if (chat?.id) upsertChat(chat);

  const thread = state.thread;
  if (thread && thread.id === message.chat_id && !thread.loading) {
    const wasNearBottom = distanceFromBottom() < NEAR_BOTTOM_PX;
    const result = applyToThread(thread, message);
    renderMessages();
    renderComposerContext();
    if (result === "new") {
      if (wasNearBottom || message.from_me) scrollToBottom(true);
      else showNewPill();
      if (!message.from_me && !message.event) {
        const source = chat ?? currentChat();
        const who = source?.is_group ? personName(resolvePerson(message.sender, source)) : chatTitle(source);
        announce(`New message from ${who}: ${truncate(messageSummary(message) || "Attachment", 200)}`);
      }
    }
    maybeMarkRead();
  } else if (thread && thread.id === message.chat_id) {
    // The first page is still loading; keep the update so the page merge sees it.
    applyToThread(thread, message);
  }
  maybeNotify(message, chat, previousUnread);
}

// --------------------------------------------------------------------- calls

function callerPerson(call) {
  const caller = call.caller;
  if (!caller) return null;
  if (caller.name?.trim()) return caller;
  const key = addressKey(caller.address);
  for (const chat of state.chats) {
    const match = chat.participants?.find((p) => addressKey(p.address) === key && p.name?.trim());
    if (match) return match;
  }
  return caller;
}

// A call placed from this PC only moves forward; a late reply must not undo a newer state.
const OUTGOING_ORDER = ["outgoing", "answered", "ready", "connected", "ended"];

function onCallEvent(call) {
  if (call.outgoing) {
    onOutgoingCall(call);
    return;
  }
  const current = state.call;
  if (call.status === "incoming") {
    if (!feature("facetime")) return;
    if (current?.call.id === call.id) {
      current.call = call;
    } else {
      if (current) clearTimeout(current.timer);
      state.call = { call, answering: false, timer: 0 };
      notifyCall(call);
    }
    clearTimeout(state.call.timer);
    state.call.timer = setTimeout(hideCall, CALL_TIMEOUT_MS);
    renderCall();
  } else if (current && current.call.id === call.id && (call.status === "ended" || call.status === "connected")) {
    hideCall();
  }
}

function onOutgoingCall(call) {
  let current = state.call;
  const finished = call.status === "connected" || call.status === "ended";
  if (current?.call.id === call.id) {
    if (OUTGOING_ORDER.indexOf(call.status) < OUTGOING_ORDER.indexOf(current.call.status)) return;
    current.call = call;
  } else {
    if (finished) return;
    if (current) clearTimeout(current.timer);
    current = { call, answering: false, timer: 0, outgoing: true, joined: false };
    state.call = current;
  }
  if (call.status === "ready" && !current.joined) {
    current.joined = true;
    joinPlacedCall(current);
  }
  if (call.status === "connected") {
    hideCall();
    toast("The call is on this PC now. Hang up in the FaceTime window.", { duration: LONG_TOAST_MS });
    return;
  }
  if (call.status === "ended") {
    hideCall();
    toast(call.detail || "Call ended.");
    return;
  }
  renderCall();
}

async function joinPlacedCall(current) {
  try {
    openFaceTime(await api.joinCall(current.call.id));
  } catch (err) {
    current.joined = false;
    if (err.status !== 401) toast(`Couldn't open the call: ${err.message}`);
  }
}

function outgoingCallText(call) {
  const kind = call.is_video ? "FaceTime Video" : "FaceTime Audio";
  if (call.status === "answered") return "Answered. Moving the call to this PC...";
  if (call.status === "ready") return "Click Join in the FaceTime window";
  return `${kind} · Ringing...`;
}

// The desktop app shows calls in its own floating window, above every other window.
const floatingCallWindow = () => "pywebview" in window;

function renderCall() {
  const current = state.call;
  if (!current || floatingCallWindow()) {
    if (dom.callCard.open) dom.callCard.close();
    return;
  }
  // Focus the card, not a button, so a key pressed mid-sentence can't answer or decline.
  if (!dom.callCard.open) openDialog(dom.callCard, dom.callCard);
  const person = callerPerson(current.call);
  dom.callAvatar.replaceChildren(person ? personAvatar(person, "avatar-lg") : h("span", { class: "avatar avatar-lg" }, icon("video", 20)));
  dom.callName.textContent = person ? personName(person) : "Unknown caller";
  if (current.outgoing) {
    dom.callKind.textContent = outgoingCallText(current.call);
    dom.callAnswer.hidden = true;
    dom.callDecline.disabled = false;
    dom.callDecline.setAttribute("aria-label", "End call");
    dom.callDecline.title = "End call";
    return;
  }
  dom.callAnswer.hidden = false;
  dom.callDecline.setAttribute("aria-label", "Decline");
  dom.callDecline.title = "Decline";
  dom.callKind.textContent = current.answering ? "Connecting..." : current.call.is_video ? "FaceTime Video" : "FaceTime Audio";
  dom.callAnswer.disabled = current.answering;
  dom.callDecline.disabled = current.answering;
  dom.callAnswer.setAttribute("aria-label", current.call.is_video ? "Answer video call" : "Answer call");
}

function hideCall() {
  if (state.call) clearTimeout(state.call.timer);
  state.call = null;
  renderCall();
}

function notifyCall(call) {
  if (document.hasFocus() || !canShowWebNotification()) return;
  const person = callerPerson(call);
  try {
    const notification = new Notification(person ? personName(person) : "Incoming call", {
      body: call.is_video ? "Incoming FaceTime video call" : "Incoming FaceTime audio call",
      tag: `phonebridge-call-${call.id}`,
      requireInteraction: true,
    });
    notification.addEventListener("click", () => {
      window.focus();
      notification.close();
    });
  } catch {
    // Notifications are optional; the call card is the main surface.
  }
}

async function answerCall() {
  const current = state.call;
  if (!current || current.answering) return;
  current.answering = true;
  renderCall();
  try {
    openFaceTime(await api.answerCall(current.call.id));
  } catch (err) {
    if (state.call === current) {
      current.answering = false;
      renderCall();
    }
    if (err.status !== 401) toast(`Couldn't answer: ${err.message}`);
  }
}

async function declineCall() {
  const current = state.call;
  if (!current) return;
  hideCall();
  try {
    await api.declineCall(current.call.id);
  } catch (err) {
    if (err.status !== 401) toast(`Couldn't ${current.outgoing ? "end the call" : "decline"}: ${err.message}`);
  }
}

function safeLink(link) {
  try {
    const url = new URL(link, window.location.href);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

/** FaceTime web needs Edge or Chrome; the desktop app opens Edge itself and says so with `opened`. */
function openFaceTime(result) {
  const link = safeLink(result?.link);
  if (!result?.opened) {
    if (!link) {
      toast("FaceTime didn't return a link. Try again.");
      return;
    }
    window.open(link, "_blank", "noopener");
  }
  const inEdge = result?.opened || /\bEdg\//.test(navigator.userAgent);
  toast(`FaceTime opened in ${inEdge ? "Edge" : "a new tab"}. Allow camera and microphone, then Join.`, {
    duration: LONG_TOAST_MS,
    action: !result?.opened && link ? { label: "Open again", run: () => window.open(link, "_blank", "noopener") } : null,
  });
}

async function startFaceTime(video) {
  const thread = state.thread;
  if (!thread || dom.facetimeBtn.disabled || state.call) return;
  dom.facetimeBtn.disabled = true;
  dom.audioCallBtn.disabled = true;
  try {
    onCallEvent(await api.startCall(thread.id, video));
  } catch (err) {
    if (err.status !== 401) toast(`Couldn't call: ${err.message}`);
  } finally {
    dom.facetimeBtn.disabled = false;
    dom.audioCallBtn.disabled = false;
  }
}

// ------------------------------------------------------------------- dialogs

let confirmResolve = null;

function confirmDialog({ title, text, ok, danger = true }) {
  if (confirmResolve) confirmResolve(false);
  dom.confirmTitle.textContent = title;
  dom.confirmText.textContent = text;
  dom.confirmOk.textContent = ok;
  dom.confirmOk.className = `btn ${danger ? "danger" : "primary"}`;
  return new Promise((resolve) => {
    confirmResolve = resolve;
    openDialog(dom.confirmDialog, dom.confirmDialog.querySelector("[data-close]"));
  });
}

function settleConfirm(value) {
  const resolve = confirmResolve;
  confirmResolve = null;
  resolve?.(value);
}

// --------------------------------------------------------------- new message

const compose = { recipients: [], sending: false, error: null };
let toPicker = null;

function composeMultiple() {
  return feature("new_group");
}

function openCompose() {
  if (state.locked || dom.composeDialog.open) return;
  compose.error = null;
  toPicker.close();
  renderCompose();
  openDialog(dom.composeDialog, compose.recipients.length && !composeMultiple() ? dom.composeText : dom.toInput);
}

function resetCompose() {
  compose.recipients = [];
  compose.error = null;
  toPicker.reset();
  dom.composeText.value = "";
}

function composeTargets() {
  const targets = [...compose.recipients];
  const typed = dom.toInput.value.trim();
  if (looksLikeAddress(typed) && (composeMultiple() || !targets.length)) targets.push({ address: cleanTypedAddress(typed), name: null });
  return composeMultiple() ? targets : targets.slice(0, 1);
}

function addRecipient(person) {
  if (!composeMultiple()) compose.recipients = [person];
  else if (!compose.recipients.some((r) => addressKey(r.address) === addressKey(person.address))) compose.recipients.push(person);
  compose.error = null;
  renderCompose();
  if (composeMultiple()) dom.toInput.focus();
  else dom.composeText.focus();
}

function removeRecipient(index) {
  compose.recipients.splice(index, 1);
  renderCompose();
  dom.toInput.focus();
}

function existingChatFor(targets) {
  if (!targets.length) return null;
  const keys = new Set(targets.map((t) => addressKey(t.address)));
  return (
    state.chats.find((chat) => {
      const people = chat.participants ?? [];
      if (chat.is_group !== targets.length > 1 || people.length !== keys.size) return false;
      return people.every((p) => keys.has(addressKey(p.address)));
    }) ?? null
  );
}

function renderCompose() {
  if (!dom.composeDialog || !toPicker) return;
  const multiple = composeMultiple();
  // Rebuild chips only when they change, and never move the input: moving a focused field blurs it.
  const chipSig = JSON.stringify([compose.recipients, multiple]);
  if (dom.toField.dataset.sig !== chipSig) {
    dom.toField.dataset.sig = chipSig;
    for (const el of [...dom.toField.children]) if (el !== dom.toInput) el.remove();
    compose.recipients.forEach((person, index) => {
      const label = person.name ? (multiple ? person.name : `${person.name} · ${formatAddress(person.address)}`) : formatAddress(person.address);
      const chip = h(
        "span",
        { class: "recipient-chip" },
        h("span", { title: formatAddress(person.address) }, label),
        h("button", { type: "button", "aria-label": `Remove ${personName(person)}`, onclick: () => removeRecipient(index) }, icon("close", 14)),
      );
      dom.toField.insertBefore(chip, dom.toInput);
    });
  }
  dom.toInput.hidden = !multiple && compose.recipients.length > 0;
  dom.toInput.placeholder = compose.recipients.length ? "Add someone else" : "Name, phone number or email";

  const existing = existingChatFor(compose.recipients);
  dom.composeNote.hidden = !existing;
  if (existing) {
    dom.composeNote.replaceChildren(
      h("span", {}, existing.is_group ? "You already have a group with these people." : `You already have a conversation with ${chatTitle(existing)}.`),
      h("button", { type: "button", class: "link-btn", onclick: () => openExistingFromCompose(existing) }, "Open it"),
    );
  }

  const connected = isConnected();
  const targets = composeTargets();
  dom.composeHint.hidden = connected || !state.status;
  dom.composeHintText.textContent = composerHintText();
  dom.composeError.hidden = !compose.error;
  dom.composeError.textContent = compose.error ?? "";
  dom.composeSend.disabled = !connected || !targets.length || !dom.composeText.value.trim() || compose.sending;
  dom.composeSend.textContent = compose.sending ? "Sending..." : targets.length > 1 ? "Send to group" : "Send";
  dom.composeText.placeholder = mode() === "phone" ? "Text message" : "Message";
}

function openExistingFromCompose(chat) {
  const text = dom.composeText.value;
  if (text.trim() && !state.drafts.get(chat.id)) state.drafts.set(chat.id, text);
  resetCompose();
  closeDialogQuietly(dom.composeDialog);
  openConversation(chat.id, { focusComposer: true });
}

async function sendCompose() {
  const targets = composeTargets();
  const text = dom.composeText.value.trim();
  if (!targets.length || !text || !isConnected() || compose.sending) return;
  compose.sending = true;
  compose.error = null;
  renderCompose();
  const sinceSeq = state.eventSeq;
  let page;
  try {
    page = await api.createChat(
      targets.map((t) => t.address),
      text,
    );
  } catch (err) {
    compose.sending = false;
    compose.error = err.status === 401 ? null : `Couldn't send: ${err.message}`;
    renderCompose();
    return;
  }
  compose.sending = false;
  resetCompose();
  closeDialogQuietly(dom.composeDialog);
  if (!page?.chat?.id) {
    await loadChats().catch(() => {});
    return;
  }
  upsertChat(page.chat);
  await openConversation(page.chat.id, { page, pageSeq: sinceSeq, focusComposer: true });
}

// ------------------------------------------------------------------ chat info

let addPicker = null;

function manageable(chat) {
  return Boolean(chat?.is_group) && feature("manage_groups");
}

function openInfo() {
  if (!state.thread) return;
  renderInfo();
  dom.renameInput.value = currentRenameValue();
  openDialog(dom.infoDialog);
}

function currentRenameValue() {
  const chat = currentChat();
  return chat?.has_custom_name ? chat.title : "";
}

function renderInfo() {
  const thread = state.thread;
  if (!thread) return;
  const chat = chatOrStub(thread.id);
  const people = chat.participants ?? [];
  const manage = manageable(chat);
  dom.infoAvatar.replaceChildren(chatAvatar(chat, "avatar-xl"));
  dom.infoName.textContent = chatTitle(chat);
  const service = chat.service ? ` · ${chat.service}` : "";
  dom.infoSub.textContent = chat.is_group ? `${people.length + 1} people${service}` : `${formatAddress(people[0]?.address) || ""}${service}`;
  dom.infoRename.hidden = !manage;
  if (manage && document.activeElement !== dom.renameInput && !dom.renameInput.dataset.dirty) dom.renameInput.value = currentRenameValue();
  dom.peopleLabel.textContent = chat.is_group ? `People (${people.length + 1})` : "Contact";
  const canRemove = manage && people.length > 2;
  dom.peopleList.replaceChildren(
    ...people.map((person) => {
      const row = h(
        "li",
        { class: "person-row" },
        personAvatar(person, "avatar-sm"),
        h("span", { class: "person-text" }, h("span", { class: "person-name" }, personName(person)), person.name ? h("span", { class: "person-addr" }, formatAddress(person.address)) : null),
        h(
          "button",
          {
            type: "button",
            class: "icon-btn small",
            "aria-label": `Copy ${person.address.includes("@") ? "email address" : "phone number"} of ${personName(person)}`,
            title: "Copy",
            onclick: async () => toast((await copyText(person.address)) ? "Copied" : "Couldn't copy. Select the text instead."),
          },
          icon("copy", 16),
        ),
      );
      if (canRemove) {
        row.append(
          h(
            "button",
            { type: "button", class: "icon-btn small danger-icon", "aria-label": `Remove ${personName(person)} from the group`, title: "Remove from group", onclick: () => removePerson(person) },
            icon("minus", 16),
          ),
        );
      }
      return row;
    }),
  );
  if (chat.is_group) {
    dom.peopleList.append(h("li", { class: "person-row me" }, h("span", { class: "avatar avatar-sm av-7", "aria-hidden": "true" }, "You"), h("span", { class: "person-text" }, h("span", { class: "person-name" }, "You"))));
  }
  dom.addPerson.hidden = !manage;
  dom.leaveBtn.hidden = !manage;
  dom.leaveBtn.textContent = "Leave this conversation";
}

async function renameGroup() {
  const chat = currentChat();
  if (!chat || !manageable(chat)) return;
  const title = dom.renameInput.value.trim();
  if (title === (chat.has_custom_name ? chat.title : "")) return;
  dom.renameSave.disabled = true;
  try {
    const updated = await api.rename(chat.id, title);
    delete dom.renameInput.dataset.dirty;
    if (updated?.id) upsertChat(updated);
    toast(title ? "Group renamed." : "Group name removed.");
  } catch (err) {
    if (err.status !== 401) toast(`Couldn't rename: ${err.message}`);
  } finally {
    dom.renameSave.disabled = false;
  }
}

async function addPerson(person) {
  const chat = currentChat();
  if (!chat || !manageable(chat)) return;
  dom.addInput.disabled = true;
  try {
    const updated = await api.addParticipant(chat.id, person.address);
    if (updated?.id) upsertChat(updated);
    toast(`Added ${personName(person)}.`);
  } catch (err) {
    if (err.status !== 401) toast(`Couldn't add ${personName(person)}: ${err.message}`);
  } finally {
    dom.addInput.disabled = false;
    dom.addInput.focus();
  }
}

async function removePerson(person) {
  const chat = currentChat();
  if (!chat) return;
  const ok = await confirmDialog({
    title: `Remove ${personName(person)}?`,
    text: `They'll stop getting messages from “${chatTitle(chat)}”.`,
    ok: "Remove",
  });
  if (!ok) return;
  try {
    const updated = await api.removeParticipant(chat.id, person.address);
    if (updated?.id) upsertChat(updated);
    toast(`Removed ${personName(person)}.`);
  } catch (err) {
    if (err.status !== 401) toast(`Couldn't remove: ${err.message}`);
  }
}

async function leaveGroup() {
  const chat = currentChat();
  if (!chat) return;
  const ok = await confirmDialog({
    title: "Leave this conversation?",
    text: `You'll stop getting messages from “${chatTitle(chat)}”.`,
    ok: "Leave",
  });
  if (!ok) return;
  dom.leaveBtn.disabled = true;
  try {
    state.leaving.add(chat.id);
    await api.leave(chat.id);
    if (dom.infoDialog.open) closeDialogQuietly(dom.infoDialog);
  } catch (err) {
    state.leaving.delete(chat.id);
    if (err.status !== 401) toast(`Couldn't leave: ${err.message}`);
  } finally {
    dom.leaveBtn.disabled = false;
  }
}

// -------------------------------------------------------------------- locked

function showLocked() {
  if (state.locked) return;
  state.locked = true;
  clearTimeout(state.socketTimer);
  const socket = state.socket;
  state.socket = null;
  socket?.close();
  closeMenu({ restoreFocus: false });
  hideCall();
  for (const dialog of document.querySelectorAll("dialog[open]")) dialog.close();
  dom.app.hidden = true;
  dom.connIndicator.hidden = true;
  dom.notifyCard.hidden = true;
  dom.locked.hidden = false;
  document.title = `Locked - ${APP_NAME}`;
}

// --------------------------------------------------------------------- toast

let toastTimer = 0;

/** Popover support puts the toast in the top layer, above any open dialog; older browsers just skip it. */
function setToastVisible(visible) {
  dom.toast.hidden = !visible;
  if (typeof dom.toast.showPopover !== "function") return;
  try {
    if (dom.toast.matches(":popover-open")) dom.toast.hidePopover();
    if (visible) dom.toast.showPopover();
  } catch {
    // The toast still shows in the page, just under a dialog's backdrop.
  }
}

function toast(text, { action = null, duration = TOAST_MS } = {}) {
  clearTimeout(toastTimer);
  dom.toast.replaceChildren(h("span", {}, text));
  if (action) {
    dom.toast.append(
      h(
        "button",
        {
          type: "button",
          class: "toast-action",
          onclick: () => {
            setToastVisible(false);
            action.run();
          },
        },
        action.label,
      ),
    );
  }
  setToastVisible(true);
  toastTimer = setTimeout(() => setToastVisible(false), duration);
}

// --------------------------------------------------------------------- theme

const THEMES = new Set(["system", "light", "dark"]);

function applyTheme(theme) {
  const value = THEMES.has(theme) ? theme : "system";
  if (value === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = value;
  for (const input of document.querySelectorAll('input[name="theme"]')) input.checked = input.value === value;
}

// -------------------------------------------------------------------- events

function bindEvents() {
  dom.statusPill.addEventListener("click", () => openSettings());
  dom.settingsBtn.addEventListener("click", () => openSettings());
  dom.macHintOpen.addEventListener("click", () => openSettings({ focusMac: true }));
  dom.macHintDismiss.addEventListener("click", () => {
    storage.set(STORAGE_KEYS.macHintHidden, "1");
    renderMacHint();
    dom.searchInput.focus();
  });
  dom.newMessageBtn.addEventListener("click", () => openCompose());
  dom.emptyNewBtn.addEventListener("click", () => openCompose());

  dom.searchInput.addEventListener("input", onSearchInput);
  dom.searchInput.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      if (dom.searchInput.value) clearSearch();
      else dom.searchInput.blur();
    } else if (event.key === "ArrowDown") {
      const list = filteredChats();
      if (!list.length) return;
      event.preventDefault();
      dom.convList.focus();
      dom.convList.classList.add("kbd");
      setActiveConversation(list[0].id, { scroll: true });
    } else if (event.key === "Enter") {
      event.preventDefault();
      const first = filteredChats()[0];
      if (first) openConversation(first.id, { focusComposer: true });
      else dom.hitList.querySelector("button")?.click();
    }
  });
  dom.hitList.addEventListener("click", onHitClick);

  dom.convList.addEventListener("keydown", onConversationListKeydown);
  dom.convList.addEventListener("mousedown", () => dom.convList.classList.remove("kbd"));
  dom.convList.addEventListener("focus", () => {
    const list = filteredChats();
    if (!list.length || list.some((c) => c.id === state.activeId)) return;
    setActiveConversation((list.find((c) => c.id === state.openId) ?? list[0]).id);
  });
  dom.convList.addEventListener("click", (event) => {
    const row = event.target.closest(".conv-row");
    if (row) openConversation(row.dataset.id, { focusComposer: true });
  });

  dom.notifyEnable.addEventListener("click", enableNotifications);
  dom.notifyLater.addEventListener("click", dismissNotifyPrompt);
  dom.notifyDismiss.addEventListener("click", dismissNotifyPrompt);

  for (const input of document.querySelectorAll('input[name="theme"]')) {
    input.addEventListener("change", () => {
      if (!input.checked) return;
      applyTheme(input.value);
      storage.set(STORAGE_KEYS.theme, input.value);
    });
  }

  dom.backBtn.addEventListener("click", onBack);
  dom.copyAddressBtn.addEventListener("click", async () => {
    const address = currentChat()?.participants?.[0]?.address;
    if (!address) return;
    toast((await copyText(address)) ? "Copied" : "Couldn't copy. Select the text instead.");
  });
  dom.facetimeBtn.addEventListener("click", () => startFaceTime(true));
  dom.audioCallBtn.addEventListener("click", () => startFaceTime(false));
  dom.infoBtn.addEventListener("click", openInfo);

  dom.threadScroll.addEventListener("scroll", onThreadScroll, { passive: true });
  dom.messages.addEventListener("click", onMessagesClick);
  dom.messages.addEventListener("keydown", onMessagesKeydown);
  dom.messages.addEventListener("contextmenu", onMessagesContextMenu);
  dom.messages.addEventListener("pointerdown", onMessagesPointerDown);
  dom.messages.addEventListener("pointermove", onMessagesPointerMove);
  dom.messages.addEventListener("pointerup", cancelPress);
  dom.messages.addEventListener("pointercancel", cancelPress);
  dom.messages.addEventListener("focusin", (event) => {
    const row = event.target;
    if (state.thread && row instanceof HTMLElement && row.matches("li.msg[data-key]")) {
      state.thread.focusKey = row.dataset.key;
      applyRovingFocus();
    }
  });
  dom.newPill.addEventListener("click", () => scrollToBottom(true));
  // Keep the newest message in view while images load, typing bubbles appear and so on.
  // Watching the scroller too covers the composer growing (reply strip, attachments, long drafts).
  const keepAtBottom = new ResizeObserver(() => {
    const thread = state.thread;
    if (thread?.stick && !thread.loading && Date.now() >= thread.smoothUntil) dom.threadScroll.scrollTop = dom.threadScroll.scrollHeight;
  });
  keepAtBottom.observe(dom.threadInner);
  keepAtBottom.observe(dom.threadScroll);

  dom.thread.addEventListener("dragenter", onDragEnter);
  dom.thread.addEventListener("dragover", onDragOver);
  dom.thread.addEventListener("dragleave", onDragLeave);
  dom.thread.addEventListener("drop", onDrop);
  // A file dropped anywhere else would make the browser navigate away to it.
  window.addEventListener("dragover", (event) => {
    if (hasFiles(event)) event.preventDefault();
  });
  window.addEventListener("drop", (event) => {
    if (hasFiles(event)) event.preventDefault();
  });

  dom.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    sendFromComposer();
  });
  dom.composerInput.addEventListener("input", () => {
    autoGrow(dom.composerInput);
    updateComposer();
    onComposerTyping();
  });
  dom.composerInput.addEventListener("keydown", (event) => {
    if (enterSends(event)) {
      event.preventDefault();
      sendFromComposer();
    } else if (event.key === "Escape" && state.composerMode) {
      event.preventDefault();
      cancelComposerMode();
    } else if (event.key === "ArrowUp" && !dom.composerInput.value && !state.composerMode) {
      // Like most chat apps: Up in an empty composer edits your last message.
      const thread = state.thread;
      const last = thread ? [...thread.messages].reverse().find((m) => m.from_me && !m.event) : null;
      if (last && messageCaps(last, chatOrStub(thread.id)).canEdit) {
        event.preventDefault();
        startEdit(last);
      }
    }
  });
  dom.composerInput.addEventListener("paste", (event) => {
    const data = event.clipboardData;
    const files = Array.from(data?.files ?? []);
    if (!files.length || !canAttach()) return;
    if (Array.from(data.types ?? []).includes("text/plain")) return;
    event.preventDefault();
    addFiles(files.map(pastedFile));
  });
  dom.attachBtn.addEventListener("click", () => dom.fileInput.click());
  dom.fileInput.addEventListener("change", () => {
    addFiles(Array.from(dom.fileInput.files ?? []));
    dom.fileInput.value = "";
  });
  dom.ccCancel.addEventListener("click", () => {
    cancelComposerMode();
    dom.composerInput.focus();
  });

  bindDialog(dom.composeDialog, { closeOnBackdrop: false });
  bindDialog(dom.settingsDialog, { closeOnBackdrop: true });
  bindDialog(dom.infoDialog, { closeOnBackdrop: true });
  bindDialog(dom.confirmDialog, { closeOnBackdrop: true });
  bindDialog(dom.lightbox, { closeOnBackdrop: true });

  const knownPeople = () => state.chats.flatMap((chat) => chat.participants ?? []);
  const useContacts = () => feature("contacts");
  toPicker = new AddressPicker({
    input: dom.toInput,
    listbox: dom.toOptions,
    idPrefix: "to-opt",
    onPick: addRecipient,
    exclude: () => new Set(compose.recipients.map((r) => addressKey(r.address))),
    people: knownPeople,
    useContacts,
  });
  dom.toInput.addEventListener("input", () => {
    compose.error = null;
    renderCompose();
  });
  dom.toInput.addEventListener("pickerinvalid", () => {
    compose.error = "Enter a phone number or email address, or pick a contact.";
    renderCompose();
  });
  dom.toInput.addEventListener("keydown", (event) => {
    if (event.key === "Backspace" && !dom.toInput.value && compose.recipients.length) {
      event.preventDefault();
      removeRecipient(compose.recipients.length - 1);
    }
  });
  dom.composeForm.addEventListener("submit", (event) => {
    event.preventDefault();
    sendCompose();
  });
  dom.composeText.addEventListener("input", () => {
    compose.error = null;
    renderCompose();
  });
  dom.composeText.addEventListener("keydown", (event) => {
    if (enterSends(event)) {
      event.preventDefault();
      sendCompose();
    } else if (event.key === "Backspace" && !dom.composeText.value && compose.recipients.length && !composeMultiple() && dom.composeText.selectionStart === 0) {
      removeRecipient(0);
    }
  });
  dom.composeDialog.addEventListener("cancel", (event) => {
    if (toPicker.isOpen) {
      event.preventDefault();
      toPicker.close();
    }
  });

  dom.macForm.addEventListener("submit", (event) => {
    event.preventDefault();
    connectMac();
  });
  dom.macUrl.addEventListener("input", () => {
    state.macForm.dirty = true;
  });
  dom.macDisconnect.addEventListener("click", disconnectMac);
  dom.phoneLinkBtn.addEventListener("click", closePhoneLink);

  addPicker = new AddressPicker({
    input: dom.addInput,
    listbox: dom.addOptions,
    idPrefix: "add-opt",
    onPick: addPerson,
    exclude: () => new Set((currentChat()?.participants ?? []).map((p) => addressKey(p.address))),
    people: knownPeople,
    useContacts,
  });
  dom.infoDialog.addEventListener("cancel", (event) => {
    if (addPicker.isOpen) {
      event.preventDefault();
      addPicker.close();
    }
  });
  dom.infoDialog.addEventListener("close", () => {
    addPicker.reset();
    delete dom.renameInput.dataset.dirty;
  });
  dom.renameInput.addEventListener("input", () => {
    dom.renameInput.dataset.dirty = "1";
  });
  dom.infoRename.addEventListener("submit", (event) => {
    event.preventDefault();
    renameGroup();
  });
  dom.leaveBtn.addEventListener("click", leaveGroup);

  dom.confirmOk.addEventListener("click", () => {
    settleConfirm(true);
    dom.confirmDialog.close();
  });
  dom.confirmDialog.addEventListener("close", () => settleConfirm(false));

  dom.lbPrev.addEventListener("click", () => stepLightbox(-1));
  dom.lbNext.addEventListener("click", () => stepLightbox(1));
  dom.lbStage.addEventListener("click", (event) => {
    if (event.target === dom.lbStage) dom.lightbox.close();
  });
  dom.lightbox.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft") stepLightbox(-1);
    else if (event.key === "ArrowRight") stepLightbox(1);
  });
  dom.lightbox.addEventListener("close", () => {
    state.lightbox = null;
    dom.lbImg.removeAttribute("src");
  });

  dom.callAnswer.addEventListener("click", answerCall);
  dom.callDecline.addEventListener("click", declineCall);
  bindDialog(dom.callCard, { closeOnBackdrop: false });
  dom.callCard.addEventListener("cancel", (event) => {
    // Escape only hides the card here; the call keeps ringing on the Mac and iPhone.
    event.preventDefault();
    hideCall();
  });

  document.addEventListener("keydown", (event) => {
    if (state.locked || state.menu || document.querySelector("dialog[open]")) return;
    const mod = (event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey;
    const key = event.key.toLowerCase();
    if (mod && key === "k") {
      event.preventDefault();
      focusSearch();
    } else if ((mod && key === "n") || (event.altKey && !event.ctrlKey && !event.metaKey && event.code === "KeyN")) {
      // Browsers reserve Ctrl+N in normal tabs; it works in app windows, and Alt+N works everywhere.
      event.preventDefault();
      openCompose();
    }
  });

  // Shown after the first interaction has finished, so the layout never shifts under a click in progress.
  const firstInteraction = () => {
    document.removeEventListener("pointerup", firstInteraction, true);
    document.removeEventListener("keyup", firstInteraction, true);
    setTimeout(maybeShowNotifyPrompt, 600);
  };
  document.addEventListener("pointerup", firstInteraction, true);
  document.addEventListener("keyup", firstInteraction, true);
  // pywebview can appear after the page has loaded.
  window.addEventListener("pywebviewready", () => {
    dom.notifyCard.hidden = true;
  });

  window.addEventListener("focus", () => {
    reportFocus();
    maybeMarkRead();
  });
  window.addEventListener("blur", reportFocus);
  document.addEventListener("visibilitychange", () => {
    reportFocus();
    if (!document.hidden) maybeMarkRead();
  });
  window.addEventListener("resize", () => closeMenu({ restoreFocus: false }));

  window.addEventListener("popstate", () => {
    if (mobileQuery.matches && dom.app.dataset.view === "thread" && history.state?.phonebridge !== "thread") closeThread({ focusList: true });
  });

  mobileQuery.addEventListener("change", () => {
    if (state.thread) dom.app.dataset.view = "thread";
    autoGrow(dom.composerInput);
  });

  // Relative times ("14:05" vs "Mon") go stale across midnight and as time passes.
  setInterval(() => {
    renderConversationList();
    if (state.thread) renderMessages();
    if (dom.settingsDialog.open) renderSettings();
  }, 60_000);
}

// ---------------------------------------------------------------------- init

function exposeDesktopApi() {
  // Called by the desktop app when a native notification is clicked.
  window.phonebridge = {
    openConversation(chatId) {
      if (chatId == null || chatId === "") return;
      for (const dialog of document.querySelectorAll("dialog[open]:not(#call-card)")) dialog.close();
      openConversation(String(chatId), { focusComposer: true });
    },
  };
}

function init() {
  cacheDom();
  applyTheme(storage.get(STORAGE_KEYS.theme) ?? "system");
  onUnauthorized(showLocked);
  onBeforeDialogOpen(() => closeMenu({ restoreFocus: false }));
  bindEvents();
  exposeDesktopApi();
  renderStatus();
  renderConversationList();
  renderSearchHits();
  renderThread();
  renderConnIndicator();
  connectSocket();
  bootstrap();
}

init();
