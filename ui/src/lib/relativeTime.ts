// Shared relative-time formatter. Three workbench surfaces (Inbox popover,
// full Inbox page, Harness page) had near-identical copies that hardcoded
// English suffixes; centralise here so the i18n strings live in one place.
import type { TFunction } from 'i18next';

export function formatRelativeTime(iso: string | null | undefined, t: TFunction): string {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const diffSec = Math.round((Date.now() - then) / 1000);
  if (diffSec < 60) return t('common.relative.justNow');
  const mins = Math.round(diffSec / 60);
  if (mins < 60) return t('common.relative.minutesAgo', { count: mins });
  const hours = Math.round(mins / 60);
  if (hours < 24) return t('common.relative.hoursAgo', { count: hours });
  const days = Math.round(hours / 24);
  if (days < 7) return t('common.relative.daysAgo', { count: days });
  return new Date(iso).toISOString().slice(0, 10);
}
