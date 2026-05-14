import type { TFunction } from 'i18next';

import type { BackendNotice } from '@/context/ApiContext';

export type ShowToast = (
  message: string,
  type?: 'success' | 'error' | 'warning',
) => void;

/**
 * Surface server-side ``notices`` (from a save / remove response) as
 * warning toasts. Today the only code-driven notice is
 * ``cleared_custom_relay_pointer`` (Codex relay-URL cleanup when
 * switching to OAuth), but the helper is shaped to accept arbitrary
 * future codes — unknown codes fall through to a generic
 * ``codexNoticeGeneric``-style toast so the user at least sees that
 * the server reported a notable side-effect.
 *
 * Previously each provider page duplicated this switch inline.
 */
export function surfaceBackendNotices(
  notices: BackendNotice[] | undefined,
  showToast: ShowToast,
  t: TFunction,
): void {
  if (!notices || notices.length === 0) return;
  for (const notice of notices) {
    if (notice.code === 'cleared_custom_relay_pointer') {
      showToast(
        t('settings.backends.codexNoticeClearedRelayPointer', {
          provider: notice.provider_id || 'custom',
          url: notice.base_url || '',
        }),
        'warning',
      );
      continue;
    }
    showToast(
      t('settings.backends.codexNoticeGeneric', { code: notice.code }),
      'warning',
    );
  }
}
