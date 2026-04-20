export type PlatformName = string;

export type PlatformCapabilities = {
  supports_channels?: boolean;
  supports_threads?: boolean;
  supports_buttons?: boolean;
  supports_quick_replies?: boolean;
  supports_message_editing?: boolean;
  markdown_upload_returns_message_id?: boolean;
  quick_reply_single_column?: boolean;
};

export type PlatformDescriptor = {
  id: PlatformName;
  config_key?: string;
  title_key?: string;
  description_key?: string;
  credential_fields?: string[];
  capabilities?: PlatformCapabilities;
};

const LEGACY_FALLBACK_CATALOG: PlatformDescriptor[] = [
  {
    id: 'slack',
    config_key: 'slack',
    title_key: 'platform.slack.title',
    description_key: 'platform.slack.desc',
    capabilities: {
      supports_channels: true,
      supports_threads: true,
      supports_buttons: true,
      supports_quick_replies: true,
      supports_message_editing: true,
    },
  },
];

export const getPlatformCatalog = (data: any): PlatformDescriptor[] => {
  const catalog = data?.platform_catalog || data?.platforms_catalog || data?.catalog?.platforms;
  if (Array.isArray(catalog) && catalog.length > 0) {
    return catalog.filter((platform: any): platform is PlatformDescriptor => typeof platform?.id === 'string');
  }
  return LEGACY_FALLBACK_CATALOG;
};

export const getPlatformIds = (data: any): PlatformName[] => getPlatformCatalog(data).map((platform) => platform.id);

export const getEnabledPlatforms = (data: any): PlatformName[] => {
  const catalogIds = new Set(getPlatformIds(data));
  const enabled = data?.platforms?.enabled;
  if (Array.isArray(enabled) && enabled.length > 0) {
    const filtered = enabled.filter((platform: string): platform is PlatformName => catalogIds.has(platform));
    if (filtered.length > 0) return filtered;
  }
  const legacy = data?.platform;
  if (catalogIds.has(legacy)) {
    return [legacy as PlatformName];
  }
  return [getPlatformCatalog(data)[0]?.id || 'slack'];
};

export const getPrimaryPlatform = (data: any): PlatformName => {
  const enabled = getEnabledPlatforms(data);
  const primary = data?.platforms?.primary;
  if (enabled.includes(primary)) {
    return primary as PlatformName;
  }
  return enabled[0] || getPlatformCatalog(data)[0]?.id || 'slack';
};

export const getPlatformDescriptor = (data: any, platform: string): PlatformDescriptor | undefined =>
  getPlatformCatalog(data).find((descriptor) => descriptor.id === platform);

export const platformHasCapability = (
  data: any,
  platform: string,
  capability: keyof PlatformCapabilities
): boolean => !!getPlatformDescriptor(data, platform)?.capabilities?.[capability];

export const platformSupportsChannels = (data: any, platform: string): boolean =>
  platformHasCapability(data, platform, 'supports_channels');
