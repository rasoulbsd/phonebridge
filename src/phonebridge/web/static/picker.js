// Recipient combobox: suggests contacts and people from known chats, or
// accepts a typed phone number or email. Used by New message and Add person.

import { api } from "./api.js";
import { addressKey, cleanTypedAddress, formatAddress, h, looksLikeAddress } from "./lib.js";
import { personAvatar } from "./render.js";

const CONTACT_DEBOUNCE_MS = 150;
const MAX_OPTIONS = 12;

/** Combobox that suggests contacts and known people, or accepts a typed number or email. */
export class AddressPicker {
  /**
   * @param {{input: HTMLInputElement, listbox: HTMLElement, idPrefix: string, onPick: (person: object) => void,
   *          exclude?: () => Set<string>, people?: () => Iterable<object>, useContacts?: () => boolean}} options
   */
  constructor({ input, listbox, idPrefix, onPick, exclude, people, useContacts }) {
    this.input = input;
    this.listbox = listbox;
    this.idPrefix = idPrefix;
    this.onPick = onPick;
    this.exclude = exclude ?? (() => new Set());
    this.people = people ?? (() => []);
    this.useContacts = useContacts ?? (() => true);
    this.options = [];
    this.active = -1;
    this.seq = 0;
    this.timer = 0;
    input.addEventListener("input", () => this.onInput());
    input.addEventListener("keydown", (event) => this.onKeydown(event));
    input.addEventListener("blur", () => this.close());
  }

  get isOpen() {
    return this.options.length > 0;
  }

  reset() {
    this.seq += 1;
    clearTimeout(this.timer);
    this.input.value = "";
    this.close();
  }

  close() {
    this.options = [];
    this.active = -1;
    this.render();
  }

  onInput() {
    const query = this.input.value.trim();
    clearTimeout(this.timer);
    this.seq += 1;
    this.options = query ? this.build(query, []) : [];
    this.active = this.options.length ? 0 : -1;
    this.render();
    if (!query || !this.useContacts()) return;
    const seq = this.seq;
    this.timer = setTimeout(async () => {
      try {
        const contacts = await api.contacts(query);
        if (seq !== this.seq) return;
        this.options = this.build(query, Array.isArray(contacts) ? contacts : []);
        this.active = this.options.length ? 0 : -1;
        this.render();
      } catch {
        // Contacts are a convenience; local matches and typed numbers still work.
      }
    }, CONTACT_DEBOUNCE_MS);
  }

  build(query, contacts) {
    const options = [];
    const seen = this.exclude();
    const add = (address, name, raw = false) => {
      const key = addressKey(address);
      if (!key || seen.has(key)) return;
      seen.add(key);
      options.push({ address, name: name || null, raw });
    };
    for (const contact of contacts) {
      for (const address of contact.addresses ?? []) add(address, contact.name);
    }
    const lower = query.toLowerCase();
    const digits = query.replace(/\D/g, "");
    for (const person of this.people()) {
      if (options.length >= MAX_OPTIONS) break;
      const nameHit = person.name?.toLowerCase().includes(lower);
      const address = person.address ?? "";
      const addressHit = address.toLowerCase().includes(lower) || (digits.length >= 3 && address.replace(/\D/g, "").includes(digits));
      if (nameHit || addressHit) add(address, person.name);
    }
    if (looksLikeAddress(query)) add(cleanTypedAddress(query), null, true);
    return options.slice(0, MAX_OPTIONS);
  }

  render() {
    const open = this.options.length > 0;
    this.listbox.hidden = !open;
    this.input.setAttribute("aria-expanded", String(open));
    if (!open) {
      this.listbox.replaceChildren();
      this.input.removeAttribute("aria-activedescendant");
      return;
    }
    this.listbox.replaceChildren(
      ...this.options.map((option, index) => {
        const secondary = option.name ? formatAddress(option.address) : option.raw ? "Use this number or email" : "Not in your contacts";
        return h(
          "li",
          {
            id: `${this.idPrefix}-${index}`,
            class: `to-option${index === this.active ? " active" : ""}`,
            role: "option",
            "aria-selected": String(index === this.active),
            onmousedown: (event) => event.preventDefault(),
            onclick: () => this.pick(option),
          },
          personAvatar(option, "avatar-sm"),
          h("span", { class: "to-option-text" }, h("span", { class: "to-option-name" }, option.name || formatAddress(option.address)), h("span", { class: "to-option-addr" }, secondary)),
        );
      }),
    );
    if (this.active >= 0) {
      this.input.setAttribute("aria-activedescendant", `${this.idPrefix}-${this.active}`);
      this.listbox.children[this.active]?.scrollIntoView({ block: "nearest" });
    } else {
      this.input.removeAttribute("aria-activedescendant");
    }
  }

  pick(option) {
    this.seq += 1;
    clearTimeout(this.timer);
    this.input.value = "";
    this.close();
    this.onPick({ address: option.address, name: option.name ?? null });
  }

  /** Returns true when the typed text was turned into a pick. */
  pickTyped() {
    const typed = this.input.value;
    if (!looksLikeAddress(typed)) return false;
    this.pick({ address: cleanTypedAddress(typed), name: null });
    return true;
  }

  onKeydown(event) {
    const count = this.options.length;
    switch (event.key) {
      case "ArrowDown":
        if (!count) return;
        event.preventDefault();
        this.active = (this.active + 1) % count;
        this.render();
        break;
      case "ArrowUp":
        if (!count) return;
        event.preventDefault();
        this.active = this.active <= 0 ? count - 1 : this.active - 1;
        this.render();
        break;
      case "Enter": {
        if (event.isComposing) return;
        event.preventDefault();
        const option = this.options[this.active];
        if (option) this.pick(option);
        else if (!this.pickTyped() && this.input.value.trim()) this.input.dispatchEvent(new CustomEvent("pickerinvalid"));
        break;
      }
      case ",":
      case ";":
        if (this.pickTyped()) event.preventDefault();
        break;
      case "Escape":
        if (count) {
          // Close the suggestions first; a second Escape closes the dialog.
          event.preventDefault();
          event.stopPropagation();
          this.close();
        }
        break;
      default:
        break;
    }
  }
}
