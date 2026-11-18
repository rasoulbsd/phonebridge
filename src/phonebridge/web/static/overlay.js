// Floating call window for the desktop app. The app shows and hides this window around calls;
// this page only keeps its content in step with the same live updates the main window gets.

import { accessToken, api } from "./api.js";
import { personName } from "./lib.js";
import { personAvatar } from "./render.js";

const RECONNECT_MS = 2000;
// A call placed from this PC only moves forward; a late update must not undo a newer state.
const OUTGOING_ORDER = ["outgoing", "answered", "ready", "connected", "ended"];

const dom = {
  avatar: document.getElementById("ov-avatar"),
  name: document.getElementById("ov-name"),
  status: document.getElementById("ov-status"),
  answer: document.getElementById("ov-answer"),
  decline: document.getElementById("ov-decline"),
};

let current = null;
let note = null;
let busy = false;
const joined = new Set();

function statusText(call) {
  if (note) return note;
  const kind = call.is_video ? "FaceTime Video" : "FaceTime Audio";
  switch (call.status) {
    case "incoming":
    case "ringing":
      return `Incoming ${kind}`;
    case "outgoing":
      return `${kind} · Ringing...`;
    case "answered":
      return "Answered. Moving the call to this PC...";
    case "ready":
      return "Click Join in the FaceTime window";
    case "connected":
      return call.outgoing ? "On this PC. Hang up in FaceTime." : "Connected";
    default:
      return call.detail || "Call ended";
  }
}

function render() {
  const call = current;
  if (!call) return;
  const person = call.caller;
  dom.avatar.replaceChildren(personAvatar(person ?? { address: "?" }));
  dom.name.textContent = person ? personName(person) : "Unknown caller";
  dom.status.textContent = statusText(call);
  const ringing = !call.outgoing && (call.status === "incoming" || call.status === "ringing");
  const live = call.outgoing && ["outgoing", "answered", "ready"].includes(call.status);
  dom.answer.hidden = !ringing;
  dom.decline.hidden = !(ringing || live);
  dom.decline.setAttribute("aria-label", live ? "End call" : "Decline");
  dom.decline.title = live ? "End call" : "Decline";
  dom.answer.setAttribute("aria-label", call.is_video ? "Answer video call" : "Answer call");
  dom.answer.disabled = busy;
  dom.decline.disabled = busy && ringing;
}

function onCall(call) {
  if (current?.id === call.id) {
    if (call.outgoing && OUTGOING_ORDER.indexOf(call.status) < OUTGOING_ORDER.indexOf(current.status)) return;
  } else {
    note = null;
    busy = false;
  }
  current = call;
  if (call.outgoing && call.status === "ready" && !joined.has(call.id)) {
    joined.add(call.id);
    // The main window may ask too; the app opens the call only once.
    api.joinCall(call.id).catch(() => joined.delete(call.id));
  }
  render();
}

async function answer() {
  const call = current;
  if (!call || busy) return;
  busy = true;
  note = "Connecting...";
  render();
  try {
    const result = await api.answerCall(call.id);
    note = result?.opened ? "Opened in Edge. Allow camera and microphone, then Join." : "Open the FaceTime link to join.";
  } catch (err) {
    busy = false;
    note = `Couldn't answer: ${err.message}`;
  }
  if (current === call) render();
}

async function decline() {
  const call = current;
  if (!call) return;
  note = call.outgoing ? "Ending..." : "Declining...";
  render();
  try {
    await api.declineCall(call.id);
  } catch (err) {
    note = `Couldn't ${call.outgoing ? "end the call" : "decline"}: ${err.message}`;
    if (current === call) render();
  }
}

function connect() {
  const url = new URL("/ws", window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  if (accessToken) url.searchParams.set("token", accessToken);
  const socket = new WebSocket(url);
  socket.addEventListener("message", (event) => {
    let data;
    try {
      data = JSON.parse(event.data);
    } catch {
      return;
    }
    if (data?.type === "call" && data.call?.id) onCall(data.call);
  });
  socket.addEventListener("close", () => setTimeout(connect, RECONNECT_MS));
}

dom.answer.addEventListener("click", answer);
dom.decline.addEventListener("click", decline);
connect();
