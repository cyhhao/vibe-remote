export const ALL_PLATFORMS = ['slack', 'discord', 'telegram', 'lark', 'wechat'] as const;

export type PlatformName = (typeof ALL_PLATFORMS)[number];

const PLATFORM_CREDENTIAL_CHECKS: Record<PlatformName, (data: any) => boolean> = {
  slack: (data) => !!data?.slack?.bot_token,
  discord: (data) => !!data?.discord?.bot_token,
  telegram: (data) => !!data?.telegram?.bot_token,
  lark: (data) => !!(data?.lark?.app_id && data?.lark?.app_secret),
  wechat: (data) => !!data?.wechat?.bot_token,
};

export const getEnabledPlatforms = (data: any): PlatformName[] => {
  const enabled = data?.platforms?.enabled;
  if (Array.isArray(enabled) && enabled.length > 0) {
    return enabled.filter((platform: string): platform is PlatformName =>
      (ALL_PLATFORMS as readonly string[]).includes(platform)
    );
  }
  const legacy = data?.platform;
  if ((ALL_PLATFORMS as readonly string[]).includes(legacy)) {
    return [legacy as PlatformName];
  }
  return ['slack'];
};

export const getPrimaryPlatform = (data: any): PlatformName => {
  const enabled = getEnabledPlatforms(data);
  const primary = data?.platforms?.primary;
  if ((ALL_PLATFORMS as readonly string[]).includes(primary) && enabled.includes(primary as PlatformName)) {
    return primary as PlatformName;
  }
  return enabled[0] || 'slack';
};

export const platformHasCredentials = (data: any, platform: PlatformName): boolean => {
  return PLATFORM_CREDENTIAL_CHECKS[platform](data);
};

export const hasConfiguredPlatformCredentials = (data: any): boolean => {
  return getEnabledPlatforms(data).some((platform) => platformHasCredentials(data, platform));
};

export const platformSupportsChannels = (platform: string): boolean => !['wechat'].includes(platform);
