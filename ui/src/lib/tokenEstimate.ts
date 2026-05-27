// Cheap token estimate without pulling in a 1MB tokenizer. Tuned against
// OpenAI's cl100k_base on mixed EN/CJK content: ASCII words land near
// 0.25 tokens/char, CJK characters compress to roughly 1.5 tokens each,
// whitespace barely counts. Good enough for an inline counter — anything
// that needs precise billing should round-trip through the backend
// tokenizer.
export function estimateTokens(text: string): number {
  if (!text) return 0;
  let tokens = 0;
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i);
    if (code === 0x20 || code === 0x09 || code === 0x0a || code === 0x0d) {
      tokens += 0.3;
    } else if (code > 0x7f) {
      // CJK + other non-ASCII; conservative 1.5x to avoid undercount.
      tokens += 1.5;
    } else {
      tokens += 0.25;
    }
  }
  return Math.max(1, Math.ceil(tokens));
}
