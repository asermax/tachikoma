import { ERROR_KINDS, type ErrorKind } from "../domain/agent-events.ts";

const AUTH_PATTERN = /authentication|unauthorized|invalid.?api.?key|401|403/i;

const BILLING_PATTERN =
  /billing|insufficient_quota|quota exceeded|out of budget|available balance|usage limit|credit/i;

const ENCODING_PATTERN = /surrogate|cannot encode|invalid.?utf|byte sequence|ill-formed/i;

/**
 * Transient provider/network failures pi would normally retry internally. By the
 * time one surfaces here the retry budget is spent, but the condition is still
 * inherently transient, so we mark it recoverable to keep the loop alive.
 */
const PROVIDER_PATTERN =
  /overloaded|provider.?returned.?error|rate.?limit|too many requests|429|500|502|503|504|service.?unavailable|server.?error|internal.?error|network.?error|connection.?(error|refused|lost)|websocket.?(closed|error)|other side closed|fetch failed|upstream.?connect|reset before headers|socket hang up|ended without|stream ended before|http2 request did not get a response|timed? out|timeout|terminated|retry delay/i;

const NON_RECOVERABLE_KINDS: ReadonlySet<ErrorKind> = new Set([
  ERROR_KINDS.auth,
  ERROR_KINDS.billing,
]);

export interface ClassifiedError {
  errorKind: ErrorKind;
  recoverable: boolean;
}

export const classifyErrorKind = (message: string): ErrorKind => {
  if (AUTH_PATTERN.test(message)) return ERROR_KINDS.auth;
  if (BILLING_PATTERN.test(message)) return ERROR_KINDS.billing;
  if (ENCODING_PATTERN.test(message)) return ERROR_KINDS.encoding;
  if (PROVIDER_PATTERN.test(message)) return ERROR_KINDS.provider;

  return ERROR_KINDS.unknown;
};

/**
 * Classify a failure message into a kind and whether the conversation can continue.
 * Auth and billing failures need user action and are non-recoverable; everything
 * else (encoding glitches, transient provider errors, unknown causes) is treated
 * as recoverable so the next message gets a fresh attempt.
 */
export const classifyError = (message: string): ClassifiedError => {
  const errorKind = classifyErrorKind(message);

  return { errorKind, recoverable: !NON_RECOVERABLE_KINDS.has(errorKind) };
};
