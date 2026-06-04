import type { ApiContextType } from '@/context/ApiContext';
import { isIosDevice, isStandalonePwa } from './platform';

export type WebPushSupportState =
  | { supported: true; standalone: boolean; requiresStandalone: boolean }
  | { supported: false; reason: 'unsupported' | 'ios_requires_standalone' };

function urlBase64ToArrayBuffer(value: string): ArrayBuffer {
  const padding = '='.repeat((4 - (value.length % 4)) % 4);
  const base64 = (value + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = window.atob(base64);
  const output = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) output[i] = raw.charCodeAt(i);
  return output.buffer;
}

export function getWebPushSupportState(): WebPushSupportState {
  if (typeof window === 'undefined' || typeof navigator === 'undefined') {
    return { supported: false, reason: 'unsupported' };
  }
  const hasApis = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  if (!hasApis) return { supported: false, reason: 'unsupported' };
  const standalone = isStandalonePwa();
  if (isIosDevice() && !standalone) {
    return { supported: false, reason: 'ios_requires_standalone' };
  }
  return { supported: true, standalone, requiresStandalone: isIosDevice() };
}

export async function getExistingWebPushSubscription(): Promise<PushSubscription | null> {
  if (!('serviceWorker' in navigator)) return null;
  const registration = await navigator.serviceWorker.getRegistration('/push-sw.js');
  return registration?.pushManager.getSubscription() ?? null;
}

export async function enableWebPush(api: ApiContextType): Promise<PushSubscriptionJSON> {
  const support = getWebPushSupportState();
  if (!support.supported) {
    throw new Error(support.reason);
  }
  const permission = await Notification.requestPermission();
  if (permission !== 'granted') {
    throw new Error('permission_denied');
  }

  const registration = await navigator.serviceWorker.register('/push-sw.js');
  const existing = await registration.pushManager.getSubscription();
  const subscription =
    existing ??
    (await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToArrayBuffer((await api.getWebPushVapidPublicKey()).public_key),
    }));

  const json = subscription.toJSON();
  await api.subscribeWebPush(json);
  return json;
}

export async function disableWebPush(api: ApiContextType): Promise<boolean> {
  const subscription = await getExistingWebPushSubscription();
  const endpoint = subscription?.endpoint;
  if (subscription) {
    await subscription.unsubscribe();
  }
  if (endpoint) {
    await api.unsubscribeWebPush(endpoint);
    return true;
  }
  return false;
}
