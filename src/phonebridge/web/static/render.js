// Pure DOM builders for chats, people and messages. No state, no network:
// app.js decides what to show and handles clicks through data-* attributes.

import { mediaUrl } from "./api.js";
import {
  TAPBACKS,
  addressKey,
  appendHighlighted,
  appendRichText,
  avatarClass,
  exactTime,
  fileSize,
  firstName,
  flatten,
  h,
  icon,
  initialsOf,
  listNames,
  listTime,
  personName,
  tapbackGlyph,
} from "./lib.js";

const IMAGE_MAX_W = 260;
const IMAGE_MAX_H = 320;

// --------------------------------------------------------------------- chats

export function isSmsLike(chat) {
  return chat?.service === "SMS" || chat?.service === "RCS";
}

export function chatTitle(chat) {
  if (!chat) return "Unknown";
  const title = chat.title?.trim() ?? "";
  const people = chat.participants ?? [];
  if (!chat.is_group) {
    const other = people[0];
    if (other && (!title || addressKey(title) === addressKey(other.address))) return personName(other);
    return title || "Unknown";
  }
  return title || listNames(people.map((p) => firstName(p))) || "Group";
}

/** The person behind an address, preferring the name the chat knows. */
export function resolvePerson(person, chat) {
  if (!person) return null;
  if (person.name?.trim()) return person;
  const key = addressKey(person.address);
  const known = chat?.participants?.find((p) => addressKey(p.address) === key && p.name?.trim());
  return known ?? person;
}

export function attachmentKind(attachment) {
  const mime = (attachment?.mime ?? "").toLowerCase();
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("video/")) return "video";
  if (mime.startsWith("audio/")) return "audio";
  return "file";
}

/** One-line description of a message for previews, notifications and reply strips. */
export function messageSummary(message) {
  if (!message) return "";
  if (message.event) return message.event;
  if (message.unsent) return "This message was unsent.";
  const text = flatten(message.text);
  if (text) return text;
  const attachments = message.attachments ?? [];
  if (!attachments.length) return "";
  const kinds = new Set(attachments.map(attachmentKind));
  if (attachments.length > 1) return kinds.size === 1 && kinds.has("image") ? `${attachments.length} photos` : `${attachments.length} attachments`;
  const kind = attachmentKind(attachments[0]);
  if (kind === "image") return "Photo";
  if (kind === "video") return "Video";
  if (kind === "audio") return "Audio message";
  return attachments[0].name || "Attachment";
}

// ------------------------------------------------------------------- avatars

export function personAvatar(person, sizeClass = "", { single = false } = {}) {
  const seed = addressKey(person?.address) || person?.name || "?";
  const text = initialsOf(person?.name, person?.address);
  const avatar = h("span", { class: `avatar ${sizeClass} ${avatarClass(seed)}`.trim(), "aria-hidden": "true" }, single ? Array.from(text)[0] ?? "?" : text);
  const photo = mediaUrl(person?.photo);
  if (photo) {
    // The initials stay underneath, so a photo that fails to load just disappears.
    avatar.append(h("img", { class: "avatar-photo", src: photo, alt: "", loading: "lazy", decoding: "async", onerror: (event) => event.currentTarget.remove() }));
  }
  return avatar;
}

export function chatAvatar(chat, sizeClass = "") {
  const people = chat?.participants ?? [];
  if (!chat?.is_group) return personAvatar(people[0] ?? { address: chat?.title ?? "" }, sizeClass);
  if (people.length >= 2) {
    return h(
      "span",
      { class: `avatar-stack ${sizeClass}`.trim(), "aria-hidden": "true" },
      personAvatar(people[0], "stack-back", { single: true }),
      personAvatar(people[1], "stack-front", { single: true }),
    );
  }
  return h("span", { class: `avatar group-glyph ${sizeClass} ${avatarClass(chat.id)}`.trim(), "aria-hidden": "true" }, icon("users", 20));
}

// ------------------------------------------------------------------ chat row

/**
 * @param {object} chat
 * @param {{selected: boolean, matcher: RegExp|null, typing: string|null, lastSender: object|null, lastIsEvent: boolean}} opts
 */
export function buildChatRow(chat, { selected, matcher, typing, lastSender, lastIsEvent }) {
  const unread = chat.unread > 0;
  const row = h("li", {
    class: `conv-row${unread ? " unread" : ""}`,
    role: "option",
    "aria-selected": String(selected),
    "data-id": chat.id,
  });
  if (unread) row.append(h("span", { class: "unread-dot", "aria-hidden": "true" }));
  row.append(chatAvatar(chat));

  const name = h("span", { class: "conv-name" });
  appendHighlighted(name, chatTitle(chat), matcher);
  const top = h("div", { class: "conv-top" }, name);
  if (isSmsLike(chat)) top.append(h("span", { class: "service-tag", title: `Sent as ${chat.service}` }, chat.service));
  top.append(h("time", { class: "conv-time", datetime: chat.last_at ?? "", title: exactTime(chat.last_at) }, listTime(chat.last_at)));

  const preview = h("span", { class: "conv-preview" });
  if (typing) {
    preview.classList.add("typing-text");
    preview.append(typing);
  } else {
    if (lastIsEvent) preview.classList.add("event-text");
    else if (chat.last_from_me) preview.append("You: ");
    else if (chat.is_group && lastSender) preview.append(`${firstName(lastSender)}: `);
    const text = flatten(chat.last_text);
    preview.append(text ? text : h("span", { class: "muted" }, "No messages"));
  }

  const bottom = h("div", { class: "conv-bottom" }, preview);
  if (unread) {
    bottom.append(
      h("span", { class: "badge", "aria-hidden": "true" }, chat.unread > 99 ? "99+" : String(chat.unread)),
      h("span", { class: "sr-only" }, `, ${chat.unread} unread`),
    );
  }
  row.append(h("div", { class: "conv-body" }, top, bottom));
  return row;
}

export function buildHit(hit, chat, matcher, snippetText) {
  const { message } = hit;
  const text = h("span", { class: "hit-snippet" });
  if (message.from_me) text.append("You: ");
  else if (chat.is_group) text.append(`${firstName(resolvePerson(message.sender, chat))}: `);
  appendHighlighted(text, snippetText || messageSummary(message), matcher);
  const button = h(
    "button",
    { type: "button", class: "hit", "data-chat": chat.id, "data-message": message.id },
    chatAvatar(chat, "avatar-sm"),
    h(
      "span",
      { class: "hit-body" },
      h(
        "span",
        { class: "hit-top" },
        h("span", { class: "hit-name" }, chatTitle(chat)),
        h("time", { class: "hit-date", datetime: message.at, title: exactTime(message.at) }, listTime(message.at)),
      ),
      text,
    ),
  );
  return h("li", {}, button);
}

// ------------------------------------------------------------------- messages

export function buildEventLine(message) {
  return h("li", { class: "event-line", "data-id": message.id }, h("span", { title: exactTime(message.at) }, message.event));
}

export function buildTypingRow(person, chat, showAvatar) {
  const who = person ? personName(resolvePerson(person, chat)) : chatTitle(chat);
  const row = h("li", { class: "msg in single typing-row", "aria-label": `${who} is typing` });
  const line = h("div", { class: "msg-line" });
  if (showAvatar) line.append(h("span", { class: "msg-avatar" }, person ? personAvatar(resolvePerson(person, chat), "avatar-xs") : null));
  line.append(
    h(
      "div",
      { class: "msg-main" },
      h("div", { class: "bubble typing-bubble", "aria-hidden": "true" }, h("span", { class: "dot" }), h("span", { class: "dot" }), h("span", { class: "dot" })),
    ),
  );
  row.append(line);
  return row;
}

function reactionGroups(reactions, chat) {
  const groups = new Map();
  for (const reaction of reactions ?? []) {
    if (!TAPBACKS.some((t) => t.type === reaction.type)) continue;
    let group = groups.get(reaction.type);
    if (!group) {
      group = { type: reaction.type, names: [], mine: false };
      groups.set(reaction.type, group);
    }
    if (reaction.from_me) {
      group.mine = true;
      group.names.unshift("You");
    } else {
      group.names.push(personName(resolvePerson(reaction.sender, chat)));
    }
  }
  return TAPBACKS.map((t) => groups.get(t.type)).filter(Boolean);
}

function buildReactions(message, chat) {
  const groups = reactionGroups(message.reactions, chat);
  if (!groups.length) return null;
  const wrap = h("div", { class: "reactions" });
  const spoken = [];
  for (const group of groups) {
    const meta = TAPBACKS.find((t) => t.type === group.type);
    const who = listNames(group.names);
    spoken.push(`${meta.label} from ${who}`);
    const badge = h("span", { class: `reaction${group.mine ? " mine" : ""}`, "data-type": group.type, title: `${meta.label}: ${who}` }, tapbackGlyph(group.type));
    if (group.names.length > 1) badge.append(h("span", { class: "reaction-count" }, String(group.names.length)));
    wrap.append(badge);
  }
  wrap.append(h("span", { class: "sr-only" }, `Reactions: ${spoken.join("; ")}.`));
  return wrap;
}

function mediaBox(attachment) {
  const w = attachment.width;
  const hgt = attachment.height;
  if (!(w > 0 && hgt > 0)) return null;
  const scale = Math.min(1, IMAGE_MAX_W / w, IMAGE_MAX_H / hgt);
  return { width: Math.round(w * scale), ratio: `${w} / ${hgt}` };
}

function unavailableChip(attachment, kind, reason = "Not available") {
  const iconName = { image: "image", video: "video", audio: "audio" }[kind] ?? "file";
  return h(
    "div",
    { class: "file-chip unavailable", title: attachment.name ? `${attachment.name}: ${reason}` : reason },
    h("span", { class: "file-icon" }, icon(iconName, 18)),
    h("span", { class: "file-text" }, h("span", { class: "file-name" }, attachment.name || "Attachment"), h("span", { class: "file-size" }, reason)),
  );
}

function fileChip(attachment, href) {
  const size = fileSize(attachment.size);
  return h(
    "a",
    { class: "file-chip", href, download: attachment.name || "", title: `Download ${attachment.name || "file"}`, "data-download": attachment.id },
    h("span", { class: "file-icon" }, icon(attachmentKind(attachment) === "audio" ? "audio" : "file", 18)),
    h("span", { class: "file-text" }, h("span", { class: "file-name" }, attachment.name || "Attachment"), size ? h("span", { class: "file-size" }, size) : null),
    h("span", { class: "file-dl" }, icon("download", 16)),
  );
}

function canPlay(tag, mime) {
  try {
    return document.createElement(tag).canPlayType(mime) !== "";
  } catch {
    return false;
  }
}

function buildAttachment(attachment, mediaOn) {
  const kind = attachmentKind(attachment);
  const src = mediaUrl(attachment.url);
  if (!mediaOn || !attachment.available || !src) return unavailableChip(attachment, kind);
  if (kind === "image") {
    const box = mediaBox(attachment);
    const img = h("img", {
      src: mediaUrl(attachment.thumb_url) ?? src,
      alt: "",
      loading: "lazy",
      decoding: "async",
      draggable: "false",
    });
    const button = h("button", { type: "button", class: "media-btn", "data-lightbox": attachment.id, "aria-label": `Open photo ${attachment.name || ""}`.trim() }, img);
    if (box) {
      button.classList.add("sized");
      button.style.width = `${box.width}px`;
      button.style.aspectRatio = box.ratio;
    }
    img.addEventListener("error", () => button.replaceWith(unavailableChip(attachment, kind, "Couldn't load")), { once: true });
    return button;
  }
  if (kind === "video") {
    const box = mediaBox(attachment);
    const video = h("video", { controls: true, preload: "none", playsinline: true, src, poster: mediaUrl(attachment.thumb_url), "aria-label": attachment.name || "Video" });
    const wrap = h("div", { class: "media-video" }, video);
    if (box) {
      wrap.style.width = `${box.width}px`;
      wrap.style.aspectRatio = box.ratio;
    }
    return wrap;
  }
  if (kind === "audio" && canPlay("audio", attachment.mime)) {
    return h("div", { class: "audio-chip" }, h("audio", { controls: true, preload: "none", src, "aria-label": attachment.name || "Audio message" }));
  }
  return fileChip(attachment, src);
}

function buildReplyQuote(message, chat, source) {
  const ref = message.reply_to;
  let name;
  if (source) name = source.from_me ? "You" : personName(resolvePerson(source.sender, chat));
  else name = ref.sender_name?.trim() || "You";
  const text = flatten(ref.text) || (source ? messageSummary(source) : "") || "Attachment";
  return h(
    "button",
    { type: "button", class: "reply-quote", "data-jump": ref.id, title: "Show the original message" },
    h("span", { class: "rq-name" }, name),
    h("span", { class: "rq-text" }, text),
  );
}

/**
 * @param {object} message
 * @param {{chat: object, position: string, showSender: boolean, avatar: "show"|"space"|null,
 *          meta: {kind: string, text: string}|null, highlight: RegExp|true|null,
 *          replySource: object|null, actions: boolean, canReact: boolean, canReply: boolean,
 *          media: boolean, selfKey: string}} ctx
 */
export function buildMessage(message, ctx) {
  const { chat } = ctx;
  const out = message.from_me;
  const failed = out && message.status === "failed";
  const classes = ["msg", out ? "out" : "in", ctx.position];
  if (failed) classes.push("failed");
  if (ctx.highlight) classes.push("highlight");
  if (message.unsent) classes.push("unsent");
  if ((message.reactions ?? []).length) classes.push("has-reactions");
  if (message.status === "sending") classes.push("sending");
  if (ctx.avatar) classes.push("with-avatar");
  const row = h("li", { class: classes.join(" "), "data-id": message.id, "data-key": ctx.selfKey, tabindex: "-1" });

  const sender = out ? null : resolvePerson(message.sender, chat);
  if (ctx.showSender && sender) row.append(h("div", { class: "msg-sender" }, personName(sender)));

  const line = h("div", { class: "msg-line" });
  if (ctx.avatar) line.append(h("span", { class: "msg-avatar" }, ctx.avatar === "show" && sender ? personAvatar(sender, "avatar-xs") : null));
  if (failed) line.append(h("span", { class: "fail-icon", title: message.error || "Not delivered" }, icon("alert", 18)));

  const main = h("div", { class: "msg-main" });
  if (message.reply_to && !message.unsent) main.append(buildReplyQuote(message, chat, ctx.replySource));

  const stack = h("div", { class: "msg-stack" });
  const matcher = ctx.highlight instanceof RegExp ? ctx.highlight : null;
  const who = out ? "You" : personName(sender);
  if (message.unsent) {
    stack.append(h("div", { class: "bubble unsent-bubble", title: exactTime(message.at) }, h("span", { class: "sr-only" }, `${who}: `), "This message was unsent."));
  } else {
    const attachments = message.attachments ?? [];
    if (attachments.length) {
      stack.append(h("div", { class: "attachments" }, attachments.map((a) => buildAttachment(a, ctx.media !== false))));
    }
    if (message.text) {
      const bubble = h("div", { class: "bubble", title: exactTime(message.at) }, h("span", { class: "sr-only" }, `${who}: `));
      const text = h("span", { class: "bubble-text" });
      appendRichText(text, message.text, matcher);
      bubble.append(text);
      stack.append(bubble);
    } else if (!attachments.length) {
      stack.append(h("div", { class: "bubble", title: exactTime(message.at) }, h("span", { class: "bubble-text muted" }, "Empty message")));
    }
  }
  const reactions = message.unsent ? null : buildReactions(message, chat);
  if (reactions) stack.append(reactions);
  main.append(stack);
  line.append(main);

  if (ctx.actions) {
    line.append(
      h(
        "div",
        { class: "msg-actions", "aria-hidden": "true" },
        ctx.canReact ? h("button", { type: "button", class: "act-btn", "data-act": "react", tabindex: "-1", title: "React" }, icon("react", 17)) : null,
        ctx.canReply ? h("button", { type: "button", class: "act-btn", "data-act": "reply", tabindex: "-1", title: "Reply" }, icon("reply", 17)) : null,
        h("button", { type: "button", class: "act-btn", "data-act": "more", tabindex: "-1", title: "More" }, icon("more", 17)),
      ),
    );
  }
  row.append(line);

  if (message.edited && !message.unsent) row.append(h("div", { class: "msg-note" }, "Edited"));
  row.append(h("time", { class: "msg-time", datetime: message.at }, exactTime(message.at)));

  if (ctx.meta) {
    const meta = h("div", { class: `msg-meta${ctx.meta.kind === "failed" ? " failed" : ""}`, title: ctx.meta.title ?? null });
    if (ctx.meta.kind === "failed") {
      meta.append("Failed — ", h("button", { type: "button", class: "link-btn", "data-retry": message.id }, "Retry"));
    } else {
      meta.append(ctx.meta.text);
    }
    row.append(meta);
    if (ctx.meta.kind === "failed" && message.error) row.append(h("div", { class: "msg-error" }, message.error));
  }
  return row;
}
