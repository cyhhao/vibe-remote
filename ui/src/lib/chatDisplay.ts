export const DEFAULT_CHAT_MESSAGE_FONT_SIZE = 14;
export const MIN_CHAT_MESSAGE_FONT_SIZE = 12;
export const MAX_CHAT_MESSAGE_FONT_SIZE = 20;

export function normalizeChatMessageFontSize(value: unknown): number {
  const numeric = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(numeric)) return DEFAULT_CHAT_MESSAGE_FONT_SIZE;
  return Math.max(
    MIN_CHAT_MESSAGE_FONT_SIZE,
    Math.min(MAX_CHAT_MESSAGE_FONT_SIZE, Math.round(numeric)),
  );
}
