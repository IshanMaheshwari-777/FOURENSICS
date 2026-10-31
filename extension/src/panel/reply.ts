// Puts the drafted reply into GitHub's comment box. Never submits: the maintainer
// reviews the text and presses Comment themselves. Falls back to the clipboard.

const SELECTORS = [
  "textarea#new_comment_field",
  'textarea[name="comment[body]"]',
  'textarea[aria-label*="comment" i]',
  'textarea[placeholder*="comment" i]',
  "textarea.js-comment-field",
];

function findCommentBox(): HTMLTextAreaElement | null {
  for (const sel of SELECTORS) {
    const all = Array.from(document.querySelectorAll<HTMLTextAreaElement>(sel));
    const visible = all.filter((t) => t.offsetParent !== null && !t.disabled && !t.readOnly);
    if (visible.length) return visible[visible.length - 1];
  }
  return null;
}

export async function insertReply(text: string): Promise<string> {
  const box = findCommentBox();
  if (box) {
    // React-controlled textareas ignore direct .value writes; use the native setter and an input event.
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
    const next = box.value.trim() ? `${box.value}\n\n${text}` : text;
    if (setter) setter.call(box, next);
    else box.value = next;
    box.dispatchEvent(new Event("input", { bubbles: true }));
    box.focus();
    box.scrollIntoView({ block: "center", behavior: "smooth" });
    return "Added to the comment box. Review it, then press Comment.";
  }
  try {
    await navigator.clipboard.writeText(text);
    return "Copied. Paste it into the comment box.";
  } catch {
    return "Couldn't find the comment box or copy the text.";
  }
}
