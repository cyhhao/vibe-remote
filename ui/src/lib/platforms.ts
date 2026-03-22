export const ALL_PLATFORMS = ['slack', 'discord', 'lark', 'wechat'] as const;

export type PlatformName = (typeof ALL_PLATFORMS)[number];

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

export const platformSupportsChannels = (platform: string): boolean => platform !== 'wechat';
