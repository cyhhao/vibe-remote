// Shared device / display-context detection. An iOS Home-Screen PWA has no
// browser chrome (no address bar, no back button), so a same-origin top-level
// navigation — e.g. a plain ``<a download>`` to our media proxy — has no way
// back and traps the app on the file preview. Callers that would otherwise
// navigate use these checks to pick a non-trapping path instead. Kept in one
// place so the detection can't drift between the InstallHint nudge and the
// media-download helper.

// iPhone / iPad / iPod, plus iPadOS 13+ which reports as desktop "MacIntel"
// while still exposing multi-touch.
export function isIosDevice(): boolean {
  if (typeof navigator === 'undefined') return false;
  const ua = navigator.userAgent || '';
  return (
    /iP(hone|ad|od)/.test(ua) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
  );
}

// Launched from the Home Screen / installed as a PWA: no browser chrome.
// ``navigator.standalone`` is Apple's proprietary signal; the display-mode
// media query is the cross-browser one.
export function isStandalonePwa(): boolean {
  if (typeof window === 'undefined') return false;
  return (
    (navigator as unknown as { standalone?: boolean }).standalone === true ||
    (!!window.matchMedia && window.matchMedia('(display-mode: standalone)').matches)
  );
}
