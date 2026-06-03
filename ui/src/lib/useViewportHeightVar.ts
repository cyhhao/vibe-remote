import { useEffect } from 'react';

// iOS Safari keeps the layout viewport (and 100dvh) at full height when the
// virtual keyboard opens — only the VISUAL viewport shrinks — so a bottom-pinned
// chat composer ends up stranded with a large gap above the keyboard (dvh alone
// doesn't fix it on iOS, and interactive-widget=resizes-content isn't supported
// there). Mirror window.visualViewport.height into the --app-vvh CSS var
// (rAF-throttled) so full-height surfaces can size to the actually-visible area
// and keep the input flush above the keyboard. Refs:
//   https://www.bram.us/2021/09/13/prevent-items-from-being-hidden-underneath-the-virtual-keyboard-by-means-of-the-virtualkeyboard-api/
//   https://dev.to/franciscomoretti/fix-mobile-keyboard-overlap-with-visualviewport-3a4a
export function useViewportHeightVar(): void {
  useEffect(() => {
    const vv = window.visualViewport;
    // No visualViewport (older browsers / SSR) → CSS keeps its 100dvh default.
    if (!vv) return;
    let raf = 0;
    const apply = () => {
      raf = 0;
      document.documentElement.style.setProperty('--app-vvh', `${Math.round(vv.height)}px`);
    };
    const schedule = () => {
      if (raf) return;
      raf = requestAnimationFrame(apply);
    };
    apply();
    vv.addEventListener('resize', schedule);
    vv.addEventListener('scroll', schedule);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      vv.removeEventListener('resize', schedule);
      vv.removeEventListener('scroll', schedule);
    };
  }, []);
}
