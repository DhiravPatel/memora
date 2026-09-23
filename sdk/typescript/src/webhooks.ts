/**
 * Verifying inbound webhooks from the Memory Layer.
 *
 * A webhook a receiver cannot verify is a webhook they should not trust, so this ships in
 * the SDK rather than as a snippet in the docs. Uses the Web Crypto API, so it runs in
 * Node 18+, Deno, Bun, Cloudflare Workers and Vercel Edge without a polyfill.
 */

export const SIGNATURE_HEADER = "x-memora-signature";
export const TIMESTAMP_HEADER = "x-memora-timestamp";
export const EVENT_HEADER = "x-memora-event";
export const DELIVERY_HEADER = "x-memora-delivery";

const DEFAULT_TOLERANCE_SECONDS = 300;

export interface WebhookEventEnvelope<T = Record<string, unknown>> {
  id: string;
  type: string;
  version: string;
  created_at: string;
  project_id: string;
  data: T;
}

export class WebhookVerificationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WebhookVerificationError";
  }
}

function timingSafeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) return false;
  let mismatch = 0;
  for (let index = 0; index < left.length; index += 1) {
    mismatch |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return mismatch === 0;
}

function toHex(buffer: ArrayBuffer): string {
  return [...new Uint8Array(buffer)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function hmacSha256Hex(secret: string, message: string): Promise<string> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return toHex(await crypto.subtle.sign("HMAC", key, encoder.encode(message)));
}

/** Verify a signature header against the raw request body. */
export async function verifyWebhookSignature(options: {
  secret: string;
  /** The raw body exactly as received — parse only after verifying. */
  payload: string;
  signatureHeader: string;
  toleranceSeconds?: number;
}): Promise<boolean> {
  const { secret, payload, signatureHeader } = options;
  const tolerance = options.toleranceSeconds ?? DEFAULT_TOLERANCE_SECONDS;
  if (!secret || !signatureHeader) return false;

  const parts = Object.fromEntries(
    signatureHeader
      .split(",")
      .map((piece) => piece.split("="))
      .filter((pair): pair is [string, string] => pair.length === 2),
  );
  const timestamp = parts.t;
  const provided = parts.v1;
  if (!timestamp || !provided) return false;

  const age = Math.abs(Date.now() / 1000 - Number(timestamp));
  if (!Number.isFinite(age) || age > tolerance) return false;

  const expected = await hmacSha256Hex(secret, `${timestamp}.${payload}`);
  return timingSafeEqual(expected, provided.trim());
}

/**
 * Verify and parse in one step.
 *
 * ```ts
 * const event = await constructWebhookEvent({
 *   secret: process.env.MEMORY_WEBHOOK_SECRET!,
 *   payload: await request.text(),
 *   signatureHeader: request.headers.get("x-memora-signature") ?? "",
 * });
 * if (event.type === "customer.at_risk") escalate(event.data);
 * ```
 */
export async function constructWebhookEvent<T = Record<string, unknown>>(options: {
  secret: string;
  payload: string;
  signatureHeader: string;
  toleranceSeconds?: number;
}): Promise<WebhookEventEnvelope<T>> {
  const valid = await verifyWebhookSignature(options);
  if (!valid) {
    throw new WebhookVerificationError(
      "Webhook signature did not verify. Check the secret, and make sure you are passing the raw request body.",
    );
  }
  return JSON.parse(options.payload) as WebhookEventEnvelope<T>;
}
