// HTTP client for docs/ui-api.md. Ids are opaque strings and always encoded in paths.

const PAGE_SIZE = 50;
const SEARCH_LIMIT = 50;
const CONTACT_LIMIT = 20;

// Remote access links carry ?token=...; forward it so API calls, media and the
// socket work even if the browser refuses the server's cookie.
export const accessToken = new URLSearchParams(window.location.search).get("token");

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

let unauthorized = () => {};

/** Called on any 401 so the app can show its lock screen. */
export function onUnauthorized(handler) {
  unauthorized = handler;
}

function detailMessage(data, status) {
  const detail = data?.detail;
  if (typeof detail === "string" && detail) return detail;
  // FastAPI validation errors come as a list of {msg}.
  if (Array.isArray(detail) && typeof detail[0]?.msg === "string") return detail[0].msg;
  return `Something went wrong (${status}).`;
}

async function request(path, { method = "GET", body, form } = {}) {
  const headers = { "X-Requested-With": "phonebridge", Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  let response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: form ?? (body === undefined ? undefined : JSON.stringify(body)),
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "Can't reach the iMessage app. Is it still running on your PC?");
  }
  if (response.status === 401) {
    unauthorized();
    throw new ApiError(401, "Locked");
  }
  if (!response.ok) {
    let data = null;
    try {
      data = await response.json();
    } catch {
      // Not JSON; keep the generic message.
    }
    throw new ApiError(response.status, detailMessage(data, response.status));
  }
  if (response.status === 204) return null;
  const type = response.headers.get("content-type") ?? "";
  if (type.includes("application/json")) return response.json();
  // Drain bodies we don't use (202 Accepted) so the request completes cleanly.
  await response.text().catch(() => "");
  return null;
}

const enc = encodeURIComponent;
const qs = (params) => new URLSearchParams(params).toString();

/**
 * Safe src/href for attachment URLs from the server: same-origin paths get the
 * access token, blob: previews pass through, anything else must be http(s).
 */
export function mediaUrl(url) {
  if (!url) return null;
  let parsed;
  try {
    parsed = new URL(url, window.location.href);
  } catch {
    return null;
  }
  if (parsed.protocol === "blob:") return url;
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
  if (parsed.origin !== window.location.origin) return parsed.href;
  if (accessToken && !parsed.searchParams.has("token")) parsed.searchParams.set("token", accessToken);
  return parsed.pathname + parsed.search;
}

export const api = {
  status: () => request("/api/status"),
  chats: () => request("/api/chats"),
  chat: (id) => request(`/api/chats/${enc(id)}`),
  messages(id, before) {
    const params = { limit: String(PAGE_SIZE) };
    if (before != null) params.before = before;
    return request(`/api/chats/${enc(id)}/messages?${qs(params)}`);
  },
  send: (chatId, { text, replyTo, tempId }) =>
    request(`/api/chats/${enc(chatId)}/messages`, {
      method: "POST",
      body: replyTo ? { text, reply_to: replyTo, temp_id: tempId } : { text, temp_id: tempId },
    }),
  upload(chatId, file, tempId) {
    const form = new FormData();
    form.append("file", file, file.name || "attachment");
    form.append("temp_id", tempId);
    return request(`/api/chats/${enc(chatId)}/attachments`, { method: "POST", form });
  },
  createChat: (addresses, text) => request("/api/chats", { method: "POST", body: { addresses, text } }),
  markRead: (id) => request(`/api/chats/${enc(id)}/read`, { method: "POST" }),
  typing: (id, typing) => request(`/api/chats/${enc(id)}/typing`, { method: "POST", body: { typing } }),
  rename: (id, title) => request(`/api/chats/${enc(id)}`, { method: "PATCH", body: { title } }),
  addParticipant: (id, address) => request(`/api/chats/${enc(id)}/participants`, { method: "POST", body: { address } }),
  removeParticipant: (id, address) => request(`/api/chats/${enc(id)}/participants/${enc(address)}`, { method: "DELETE" }),
  leave: (id) => request(`/api/chats/${enc(id)}/leave`, { method: "POST" }),
  react: (messageId, chatId, type, remove) =>
    request(`/api/messages/${enc(messageId)}/reactions`, {
      method: "POST",
      body: remove ? { chat_id: chatId, type, remove: true } : { chat_id: chatId, type },
    }),
  edit: (messageId, chatId, text) => request(`/api/messages/${enc(messageId)}/edit`, { method: "POST", body: { chat_id: chatId, text } }),
  unsend: (messageId, chatId) => request(`/api/messages/${enc(messageId)}/unsend`, { method: "POST", body: { chat_id: chatId } }),
  retry: (messageId, chatId) => request(`/api/messages/${enc(messageId)}/retry`, { method: "POST", body: { chat_id: chatId } }),
  search: (q) => request(`/api/search?${qs({ q, limit: String(SEARCH_LIMIT) })}`),
  contacts: (q) => request(`/api/contacts?${qs({ q, limit: String(CONTACT_LIMIT) })}`),
  startCall: (chatId, video) => request("/api/calls", { method: "POST", body: { chat_id: chatId, video } }),
  joinCall: (id) => request(`/api/calls/${enc(id)}/join`, { method: "POST" }),
  answerCall: (id) => request(`/api/calls/${enc(id)}/answer`, { method: "POST" }),
  declineCall: (id) => request(`/api/calls/${enc(id)}/decline`, { method: "POST" }),
  settings: () => request("/api/settings"),
  connectMac: (url, password) => request("/api/settings/mac", { method: "PUT", body: { url, password } }),
  disconnectMac: () => request("/api/settings/mac", { method: "DELETE" }),
  closePhoneLink: () => request("/api/phone-link/close", { method: "POST" }),
};
