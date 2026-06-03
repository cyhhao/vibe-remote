import { type RefObject, useEffect } from 'react';

// Keep a bottom-pinned composer glued to the on-screen keyboard on mobile iOS
// Safari (incl. the iOS 26 regression where `position: fixed` stops being
// honored, `dvh` under-resolves, and `visualViewport` values are
// self-contradictory — so the composer either flies off the top on focus or
// floats above the keyboard).
//
// Approach (validated on-device): do NOT fight the keyboard's open animation —
// reacting to every visualViewport frame over-corrects and flings the input even
// farther. Instead, let iOS do its natural pan, then ONCE the viewport SETTLES
// (debounced) apply a single gentle correction:
//   • size the fixed full-screen surface to the TRUE visible bottom
//     (visualViewport.offsetTop + visualViewport.height) — 100dvh under-resolves
//     with the keyboard open, which is why the composer stops short of the bottom;
//   • pull the focused field into view (scrollIntoView) as a backstop.
// On blur, restore full height and nudge the scroll to work around the iOS 26 bug
// where visualViewport.offsetTop fails to revert after the keyboard closes.
//
// Mobile only — `containerRef` is a `position: fixed` surface that exists solely
// on the mobile layout; desktop/iPad keep their normal document flow.
export function useIosKeyboardInset(containerRef: RefObject<HTMLDivElement | null>): void {
  useEffect(() => {
    // The fixed full-screen surface + document body-lock only exist on mobile.
    if (!window.matchMedia('(max-width: 767px)').matches) return;
    const vv = window.visualViewport;
    if (!vv) return;

    let settle = 0;
    const correct = () => {
      const el = containerRef.current;
      // True bottom of the visible area, in layout coords (see header comment).
      if (el) el.style.height = `${Math.round(vv.offsetTop + vv.height)}px`;
      const active = document.activeElement as HTMLElement | null;
      if (active && (active.tagName === 'TEXTAREA' || active.tagName === 'INPUT')) {
        active.scrollIntoView({ block: 'end', behavior: 'smooth' });
      }
    };
    // Debounce: nothing moves DURING the open/close animation; correct once the
    // visual viewport stops changing.
    const onViewport = () => {
      window.clearTimeout(settle);
      settle = window.setTimeout(correct, 140);
    };
    vv.addEventListener('resize', onViewport);
    vv.addEventListener('scroll', onViewport);

    const onFocusOut = () => {
      window.setTimeout(() => {
        const el = containerRef.current;
        if (el) el.style.height = '';
        // iOS 26: offsetTop can stay > 0 after dismiss; a 1px scroll forces recalc.
        if (window.visualViewport && window.visualViewport.offsetTop > 0) {
          window.scrollBy(0, -1);
          window.scrollBy(0, 1);
        }
      }, 120);
    };
    document.addEventListener('focusout', onFocusOut);

    return () => {
      window.clearTimeout(settle);
      vv.removeEventListener('resize', onViewport);
      vv.removeEventListener('scroll', onViewport);
      document.removeEventListener('focusout', onFocusOut);
      const el = containerRef.current;
      if (el) el.style.height = '';
    };
  }, [containerRef]);
}
