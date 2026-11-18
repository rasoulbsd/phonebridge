// Shared helpers: DOM building, icons, storage, formatting and text handling.
// Message text, names and addresses are untrusted, so the DOM is only ever
// built with createElement / textContent, never innerHTML.

const SVG_NS = "http://www.w3.org/2000/svg";
const AVATAR_COLORS = 8;

// ------------------------------------------------------------------- storage

export const storage = {
  get(key) {
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  set(key, value) {
    try {
      if (value == null) window.localStorage.removeItem(key);
      else window.localStorage.setItem(key, value);
    } catch {
      // Storage can be disabled or full; every caller has a sensible default.
    }
  },
};

// --------------------------------------------------------------------- icons

const ICONS = {
  paperclip: ["M20 11.5l-7.8 7.8a5 5 0 0 1-7.1-7.1l8.2-8.2a3.4 3.4 0 0 1 4.8 4.8l-8.2 8.2a1.7 1.7 0 0 1-2.4-2.4l7.4-7.4"],
  alert: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M12 7.5V13", "M12 16.5h.01"],
  bluetooth: ["M7 7.5l10 9-5 4.5V3l5 4.5-10 9"],
  message: ["M5 5h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-7l-4.5 3.5V17H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z", "M8 10h8", "M8 13h5"],
  cloudOff: ["M4 4l16 16", "M8.5 8.6A4.5 4.5 0 0 0 7 17h9.5", "M11 6.2A6 6 0 0 1 18 11a3.5 3.5 0 0 1 2.4 5.6"],
  close: ["M6 6l12 12", "M18 6L6 18"],
  reply: ["M9.5 14.5L4.5 9.5l5-5", "M4.5 9.5H15a5 5 0 0 1 0 10h-2.5"],
  react: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M8.5 14a4.2 4.2 0 0 0 7 0", "M9.2 9.6h.01", "M14.8 9.6h.01"],
  more: { paths: ["M5.5 12h.01", "M12 12h.01", "M18.5 12h.01"], width: 3 },
  copy: ["M9.5 8.5h9a1.5 1.5 0 0 1 1.5 1.5v9a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 8 19v-9a1.5 1.5 0 0 1 1.5-1.5z", "M16 8.5v-3A1.5 1.5 0 0 0 14.5 4h-9A1.5 1.5 0 0 0 4 5.5v9A1.5 1.5 0 0 0 5.5 16H8"],
  edit: ["M16.5 4.5a2.1 2.1 0 0 1 3 3L8 19l-4 1 1-4z", "M14.5 6.5l3 3"],
  unsend: ["M4 9h11a5 5 0 0 1 0 10H9", "M8 5L4 9l4 4"],
  download: ["M12 4v11", "M7 10.5l5 5 5-5", "M5 20h14"],
  file: ["M14 3H7.5A2.5 2.5 0 0 0 5 5.5v13A2.5 2.5 0 0 0 7.5 21h9a2.5 2.5 0 0 0 2.5-2.5V8z", "M14 3v5h5"],
  image: ["M5.5 4h13A1.5 1.5 0 0 1 20 5.5v13a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18.5v-13A1.5 1.5 0 0 1 5.5 4z", "M4 16l4.5-4.5 4 4 2.5-2.5L20 18", "M15 8.5h.01"],
  video: ["M4 7h10a1.5 1.5 0 0 1 1.5 1.5v7A1.5 1.5 0 0 1 14 17H4a1.5 1.5 0 0 1-1.5-1.5v-7A1.5 1.5 0 0 1 4 7z", "M15.5 10.5l5-3v9l-5-3"],
  audio: ["M9 18V6l10-2v12", "M9 18a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z", "M19 16a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z"],
  phone: ["M6.6 3.5h2.6l1.5 4-2 1.3a10.5 10.5 0 0 0 6.5 6.5l1.3-2 4 1.5v2.6a1.6 1.6 0 0 1-1.7 1.6A15.5 15.5 0 0 1 5 5.2a1.6 1.6 0 0 1 1.6-1.7z"],
  info: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M12 11v5", "M12 7.5h.01"],
  users: ["M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z", "M2.5 20a6.5 6.5 0 0 1 13 0", "M16 4.4a3.5 3.5 0 0 1 0 6.3", "M18.5 14a6.5 6.5 0 0 1 3 6"],
  userPlus: ["M10 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z", "M3.5 20a6.5 6.5 0 0 1 13 0", "M19 8v6", "M16 11h6"],
  minus: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M8 12h8"],
  leave: ["M9.5 4H6.5A2.5 2.5 0 0 0 4 6.5v11A2.5 2.5 0 0 0 6.5 20h3", "M15.5 16l4-4-4-4", "M19.5 12H9.5"],
  chevronLeft: ["M15 5l-7 7 7 7"],
  chevronRight: ["M9 5l7 7-7 7"],
  check: ["M5 12.5l4.5 4.5L19 7.5"],
  laptop: ["M5 5.5h14a1 1 0 0 1 1 1V16H4V6.5a1 1 0 0 1 1-1z", "M2 19h20"],
  settings: [
    "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
    "M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
  ],
};

export function icon(name, size = 18) {
  const spec = ICONS[name] ?? [];
  const paths = Array.isArray(spec) ? spec : spec.paths;
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", String(Array.isArray(spec) ? 1.8 : spec.width));
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.classList.add("icon");
  svg.style.width = `${size}px`;
  svg.style.height = `${size}px`;
  for (const d of paths) {
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

// ------------------------------------------------------------------ tapbacks

const HEART = "M12 20.3s-7.6-4.6-9.3-9.5C1.5 7.3 3.8 4.2 7.1 4.2c2 0 3.7 1.1 4.9 2.8 1.2-1.7 2.9-2.8 4.9-2.8 3.3 0 5.6 3.1 4.4 6.6-1.7 4.9-9.3 9.5-9.3 9.5z";
const THUMB = "M7.5 10.2l3.7-6.7a1 1 0 0 1 .9-.5 2.2 2.2 0 0 1 2.1 2.7L13.6 9h5a2 2 0 0 1 2 2.3l-1.2 7.3a2 2 0 0 1-2 1.7H7.5zM3.8 10.2h2.2v10.1H3.8a.8.8 0 0 1-.8-.8V11a.8.8 0 0 1 .8-.8z";

export const TAPBACKS = [
  { type: "love", label: "Heart", verb: "loved" },
  { type: "like", label: "Thumbs up", verb: "liked" },
  { type: "dislike", label: "Thumbs down", verb: "disliked" },
  { type: "laugh", label: "Ha ha", verb: "laughed at" },
  { type: "emphasize", label: "Exclamation", verb: "emphasized" },
  { type: "question", label: "Question", verb: "questioned" },
];

/** Small glyph for a tapback: filled SVG for heart and thumbs, short text for the rest. */
export function tapbackGlyph(type) {
  if (type === "love" || type === "like" || type === "dislike") {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    svg.setAttribute("fill", "currentColor");
    svg.classList.add("tb-icon");
    if (type === "dislike") svg.classList.add("flip");
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", type === "love" ? HEART : THUMB);
    svg.append(path);
    return svg;
  }
  const text = { laugh: "HA", emphasize: "!!", question: "?" }[type] ?? "?";
  return h("span", { class: `tb-text tb-${type}`, "aria-hidden": "true" }, text);
}

// ----------------------------------------------------------------------- DOM

/** Tiny element builder. Strings become text nodes, never markup. */
export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value == null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    el.append(child);
  }
  return el;
}

/** Make parent's children exactly `nodes`, moving existing nodes instead of recreating them. */
export function reconcile(parent, nodes) {
  let cursor = parent.firstChild;
  for (const node of nodes) {
    if (node === cursor) {
      cursor = cursor.nextSibling;
      continue;
    }
    parent.insertBefore(node, cursor);
  }
  while (cursor) {
    const next = cursor.nextSibling;
    cursor.remove();
    cursor = next;
  }
}

/** Reuses rendered rows whose inputs did not change, so updates don't flicker or drop text selection. */
export class NodeCache {
  #entries = new Map();
  #generation = 0;

  begin() {
    this.#generation += 1;
  }

  get(key, signature, build) {
    let entry = this.#entries.get(key);
    if (!entry || entry.signature !== signature) {
      entry = { signature, el: build() };
      this.#entries.set(key, entry);
    }
    entry.generation = this.#generation;
    return entry.el;
  }

  prune() {
    for (const [key, entry] of this.#entries) {
      if (entry.generation !== this.#generation) this.#entries.delete(key);
    }
  }

  clear() {
    this.#entries.clear();
  }
}

// ---------------------------------------------------------------- formatting

export const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
const weekdayShortFmt = new Intl.DateTimeFormat(undefined, { weekday: "short" });
const weekdayLongFmt = new Intl.DateTimeFormat(undefined, { weekday: "long" });
const shortDateFmt = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });
const shortDateYearFmt = new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric" });
const fullDateFmt = new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric" });
const fullDateYearFmt = new Intl.DateTimeFormat(undefined, { weekday: "long", year: "numeric", month: "long", day: "numeric" });
const exactFmt = new Intl.DateTimeFormat(undefined, { dateStyle: "full", timeStyle: "short" });
const relativeFmt = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

export function toTime(iso) {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? 0 : t;
}

export function validDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Whole calendar days between date and now (0 = today, 1 = yesterday). */
function dayDiff(date, now = new Date()) {
  const a = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const b = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((b - a) / 86_400_000);
}

export function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

export function dayKey(date) {
  return `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`;
}

export function listTime(iso) {
  const date = validDate(iso);
  if (!date) return "";
  const now = new Date();
  const diff = dayDiff(date, now);
  if (diff <= 0) return timeFmt.format(date);
  if (diff <= 6) return weekdayShortFmt.format(date);
  return date.getFullYear() === now.getFullYear() ? shortDateFmt.format(date) : shortDateYearFmt.format(date);
}

export function dayLabel(date) {
  const now = new Date();
  const diff = dayDiff(date, now);
  if (diff <= 0) return "Today";
  if (diff === 1) return "Yesterday";
  if (diff <= 6) return weekdayLongFmt.format(date);
  return date.getFullYear() === now.getFullYear() ? fullDateFmt.format(date) : fullDateYearFmt.format(date);
}

export function exactTime(iso) {
  const date = validDate(iso);
  return date ? exactFmt.format(date) : "";
}

export function relativeAgo(iso) {
  const date = validDate(iso);
  if (!date) return "";
  const seconds = Math.round((date.getTime() - Date.now()) / 1000);
  const abs = Math.abs(seconds);
  if (abs < 45) return "just now";
  if (abs < 3600) return relativeFmt.format(Math.round(seconds / 60), "minute");
  if (abs < 86_400) return relativeFmt.format(Math.round(seconds / 3600), "hour");
  return relativeFmt.format(Math.round(seconds / 86_400), "day");
}

export function formatAddress(address) {
  if (!address) return "";
  const raw = address.trim();
  if (raw.includes("@")) return raw;
  const digits = raw.replace(/\D/g, "");
  if (raw.startsWith("+") && /^[+\d\s().-]+$/.test(raw) && digits.length === 11 && digits.startsWith("1")) {
    return `+1 (${digits.slice(1, 4)}) ${digits.slice(4, 7)}-${digits.slice(7)}`;
  }
  if (digits.length === 10 && /^[\d\s().-]+$/.test(raw)) {
    return `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}`;
  }
  return raw;
}

/** Comparable key for a phone number or email, so "+1 (415) 555-0142" matches "4155550142". */
export function addressKey(address) {
  const raw = (address ?? "").trim().toLowerCase();
  if (!raw) return "";
  if (raw.includes("@")) return raw;
  const digits = raw.replace(/\D/g, "");
  return digits.length > 10 ? digits.slice(-10) : digits;
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function looksLikeAddress(value) {
  const v = value.trim();
  if (EMAIL_RE.test(v)) return true;
  return /^\+?[\d\s().-]+$/.test(v) && v.replace(/\D/g, "").length >= 3;
}

export function cleanTypedAddress(value) {
  const v = value.trim();
  if (v.includes("@")) return v;
  return (v.startsWith("+") ? "+" : "") + v.replace(/\D/g, "");
}

export function personName(person) {
  const name = person?.name?.trim();
  return name || formatAddress(person?.address) || "Unknown";
}

export function firstName(person) {
  const name = person?.name?.trim();
  if (!name) return formatAddress(person?.address) || "Someone";
  return name.split(/\s+/)[0];
}

export function initialsOf(name, address) {
  const clean = name?.trim();
  if (clean) {
    const words = clean
      .split(/\s+/)
      .map((w) => w.replace(/[^\p{L}\p{N}]/gu, ""))
      .filter(Boolean);
    if (words.length) {
      const first = Array.from(words[0])[0];
      const last = words.length > 1 ? Array.from(words.at(-1))[0] : "";
      return (first + last).toUpperCase();
    }
  }
  const raw = address ?? "";
  if (raw.includes("@")) return (Array.from(raw.trim())[0] ?? "?").toUpperCase();
  const digits = raw.replace(/\D/g, "");
  return digits.slice(-2) || "#";
}

function hashString(value) {
  let hash = 2166136261;
  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

export function avatarClass(seed) {
  return `av-${hashString(String(seed ?? "")) % AVATAR_COLORS}`;
}

export function truncate(text, max) {
  const chars = Array.from(text);
  return chars.length > max ? `${chars.slice(0, max - 1).join("").trimEnd()}…` : text;
}

export function flatten(text) {
  return (text ?? "").replace(/\s+/g, " ").trim();
}

export function fileSize(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "";
  const units = ["bytes", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit === 0 || value >= 10 ? 0 : 1;
  return `${value.toFixed(digits)} ${units[unit]}`;
}

export function listNames(names) {
  if (names.length <= 1) return names[0] ?? "";
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  return `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
}

// ----------------------------------------------------------- rich text output

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Case-insensitive matcher for every word of a search query, or null. */
export function buildMatcher(query) {
  const terms = query
    .trim()
    .split(/\s+/)
    .map((t) => t.replace(/["*]/g, ""))
    .filter(Boolean)
    .sort((a, b) => b.length - a.length)
    .map(escapeRegExp);
  return terms.length ? new RegExp(terms.join("|"), "giu") : null;
}

export function appendHighlighted(parent, text, matcher) {
  if (!matcher || !text) {
    if (text) parent.append(text);
    return;
  }
  let last = 0;
  for (const match of text.matchAll(matcher)) {
    if (!match[0]) continue;
    if (match.index > last) parent.append(text.slice(last, match.index));
    parent.append(h("mark", {}, match[0]));
    last = match.index + match[0].length;
  }
  if (last < text.length) parent.append(text.slice(last));
}

const URL_RE = /\bhttps?:\/\/[^\s<>"]+/giu;

function trimUrl(url) {
  let out = url;
  for (;;) {
    if (/[.,!?;:'\]}>]$/.test(out)) {
      out = out.slice(0, -1);
      continue;
    }
    if (out.endsWith(")") && (out.match(/\(/g) ?? []).length < (out.match(/\)/g) ?? []).length) {
      out = out.slice(0, -1);
      continue;
    }
    return out;
  }
}

/** Appends text with http(s) links turned into anchors and query words wrapped in <mark>. */
export function appendRichText(parent, text, matcher) {
  let last = 0;
  for (const match of text.matchAll(URL_RE)) {
    const url = trimUrl(match[0]);
    let valid = false;
    try {
      const parsed = new URL(url);
      valid = parsed.protocol === "http:" || parsed.protocol === "https:";
    } catch {
      valid = false;
    }
    if (!valid) continue;
    if (match.index > last) appendHighlighted(parent, text.slice(last, match.index), matcher);
    const link = h("a", { href: url, target: "_blank", rel: "noopener noreferrer" });
    appendHighlighted(link, url, matcher);
    parent.append(link);
    last = match.index + url.length;
  }
  if (last < text.length) appendHighlighted(parent, text.slice(last), matcher);
}

/** Short excerpt that keeps the first query match visible. */
export function snippet(text, matcher) {
  const flat = flatten(text);
  if (!matcher) return flat;
  matcher.lastIndex = 0;
  const match = matcher.exec(flat);
  matcher.lastIndex = 0;
  if (!match || match.index < 48) return flat;
  const from = flat.lastIndexOf(" ", match.index - 24);
  return `…${flat.slice(from > 0 ? from + 1 : match.index - 24)}`;
}

// ------------------------------------------------------------------ clipboard

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = h("textarea", { "aria-hidden": "true" });
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    area.remove();
    return ok;
  }
}
