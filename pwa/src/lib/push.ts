import { getVapidPublicKey, subscribePush } from "./api";

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

export function pushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

export function notificationPermission(): NotificationPermission | "unsupported" {
  if (!("Notification" in window)) return "unsupported";
  return Notification.permission;
}

export interface EnablePushResult {
  ok: boolean;
  message: string;
}

export async function enablePush(): Promise<EnablePushResult> {
  if (!pushSupported()) {
    return {
      ok: false,
      message: "Push is not supported here. On iOS, install the app to your home screen first.",
    };
  }
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    return { ok: false, message: `Notification permission was "${permission}".` };
  }
  let publicKey = "";
  try {
    publicKey = (await getVapidPublicKey()).public_key;
  } catch {
    publicKey = "";
  }
  if (!publicKey) {
    publicKey = import.meta.env.VITE_VAPID_PUBLIC_KEY || "";
  }
  if (!publicKey) {
    return {
      ok: false,
      message: "Server has no VAPID public key configured (set VAPID_PUBLIC_KEY).",
    };
  }
  const registration = await navigator.serviceWorker.ready;
  const existing = await registration.pushManager.getSubscription();
  const subscription =
    existing ??
    (await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(publicKey) as unknown as BufferSource,
    }));
  const json = subscription.toJSON();
  const p256dh = json.keys?.p256dh;
  const auth = json.keys?.auth;
  if (!json.endpoint || !p256dh || !auth) {
    return { ok: false, message: "Browser returned an incomplete subscription." };
  }
  await subscribePush({
    endpoint: json.endpoint,
    keys: { p256dh, auth },
  });
  return { ok: true, message: "Notifications enabled on this device." };
}
