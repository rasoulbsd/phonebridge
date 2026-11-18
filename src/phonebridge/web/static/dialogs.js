// Modal <dialog> helpers: focus trap, closing on [data-close] or the backdrop,
// and returning focus to whatever opened the dialog.

const openers = new WeakMap();
let beforeOpen = () => {};

/** Runs before any dialog opens (the app uses it to close the message menu). */
export function onBeforeDialogOpen(handler) {
  beforeOpen = handler;
}

export function openDialog(dialog, focusTarget) {
  if (dialog.open) return;
  beforeOpen();
  openers.set(dialog, document.activeElement);
  dialog.showModal();
  (focusTarget ?? dialog.querySelector("[data-close]"))?.focus();
}

export function bindDialog(dialog, { closeOnBackdrop }) {
  dialog.addEventListener("keydown", (event) => trapFocus(dialog, event));
  dialog.addEventListener("click", (event) => {
    if (event.target.closest("[data-close]")) dialog.close();
    else if (closeOnBackdrop && event.target === dialog) dialog.close();
  });
  dialog.addEventListener("close", () => {
    const opener = openers.get(dialog);
    openers.delete(dialog);
    if (opener instanceof HTMLElement && opener.isConnected) opener.focus({ preventScroll: true });
  });
}

function trapFocus(dialog, event) {
  if (event.key !== "Tab") return;
  const focusable = Array.from(dialog.querySelectorAll("button, [href], input, textarea, select, [tabindex]:not([tabindex='-1'])")).filter(
    (el) => !el.disabled && el.getClientRects().length > 0 && el.closest("dialog") === dialog,
  );
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable.at(-1);
  if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement) || document.activeElement === dialog)) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

/** Close without returning focus to the opener, for when the next step focuses something else. */
export function closeDialogQuietly(dialog) {
  openers.delete(dialog);
  dialog.close();
}
