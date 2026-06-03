// Cross-platform "is the on-screen keyboard open?" check.
//
// It must work whether the keyboard OVERLAYS the viewport (iOS Safari: window
// .innerHeight stays full, only visualViewport.height shrinks) OR RESIZES it
// (Android Chrome with interactive-widget=resizes-content: innerHeight AND
// visualViewport.height shrink together, so an innerHeight−visualViewport delta
// reads ~0 — https://developer.chrome.com/blog/viewport-resize-behavior/). The
// signal that survives both is the visual-viewport height vs the LARGEST height
// seen while no keyboard was up (the "resting" height): the keyboard only ever
// shrinks the visible viewport, and by far more than address-bar show/hide.

let restingHeight = 0;

if (typeof window !== 'undefined' && window.visualViewport) {
  const vv = window.visualViewport;
  // Initialised at module load (app start, keyboard closed) so the baseline is a
  // keyboard-free height; then tracked upward as the address bar collapses.
  restingHeight = vv.height;
  vv.addEventListener('resize', () => {
    if (vv.height > restingHeight) restingHeight = vv.height;
  });
}

// ~250–300px keyboard vs ~60–100px address-bar variation → a 150px floor cleanly
// separates "keyboard up" from address-bar collapse/expand.
const KEYBOARD_MIN_DELTA = 150;

export function isSoftKeyboardOpen(): boolean {
  if (typeof window === 'undefined' || !window.visualViewport) return false;
  return restingHeight - window.visualViewport.height > KEYBOARD_MIN_DELTA;
}
