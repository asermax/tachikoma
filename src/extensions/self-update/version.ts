/**
 * Minimal semver comparison — enough to decide "is the published version newer
 * than the one running". We compare major.minor.patch numerically and treat any
 * prerelease suffix (`-rc.1`, `-beta`) as lower than its release counterpart and
 * never as an upgrade target, so we never auto-jump onto a prerelease.
 */
export interface ParsedVersion {
  major: number;
  minor: number;
  patch: number;
  prerelease: string | null;
}

const VERSION_PATTERN = /^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?/;

export const parseVersion = (raw: string): ParsedVersion | null => {
  const match = VERSION_PATTERN.exec(raw.trim());

  if (match == null) return null;

  return {
    major: Number(match[1]),
    minor: Number(match[2]),
    patch: Number(match[3]),
    prerelease: match[4] ?? null,
  };
};

/** Negative when a < b, zero when equal core, positive when a > b. Prerelease ranks below release. */
export const compareVersions = (a: ParsedVersion, b: ParsedVersion): number => {
  if (a.major !== b.major) return a.major - b.major;
  if (a.minor !== b.minor) return a.minor - b.minor;
  if (a.patch !== b.patch) return a.patch - b.patch;

  if (a.prerelease == null && b.prerelease == null) return 0;
  if (a.prerelease == null) return 1;
  if (b.prerelease == null) return -1;

  return a.prerelease.localeCompare(b.prerelease);
};

/**
 * True when `latest` is a strictly newer *stable* release than `current`.
 * Unparseable inputs and prerelease `latest` values yield false — we stay put
 * rather than risk acting on a version we cannot reason about.
 */
export const isNewerVersion = (current: string, latest: string): boolean => {
  const parsedCurrent = parseVersion(current);
  const parsedLatest = parseVersion(latest);

  if (parsedCurrent == null || parsedLatest == null) return false;
  if (parsedLatest.prerelease != null) return false;

  return compareVersions(parsedLatest, parsedCurrent) > 0;
};
