import { ConfigError } from "./parse.ts";

const isValidTimezone = (timezone: string): boolean => {
  try {
    new Intl.DateTimeFormat(undefined, { timeZone: timezone });
    return true;
  } catch (error) {
    if (error instanceof RangeError) return false;
    throw error;
  }
};

export const systemTimezone = (): string => Intl.DateTimeFormat().resolvedOptions().timeZone;

/**
 * Unset timezone resolves to the detected system zone so cron schedules are
 * anchored to an explicit IANA name rather than croner's implicit local time.
 */
export const resolveTimezone = (timezone: string | undefined, label: string): string => {
  const resolved = timezone ?? systemTimezone();

  if (!isValidTimezone(resolved)) {
    throw new ConfigError(
      `Invalid ${label}:\n  /scheduler/timezone: '${resolved}' is not a valid IANA timezone`,
    );
  }

  return resolved;
};
