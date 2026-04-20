export type PlatformName = string;

export type PlatformCapabilities = {
  supports_channels?: boolean;
  supports_threads?: boolean;
  supports_buttons?: boolean;
  supports_quick_replies?: boolean;
  supports_message_editing?: boolean;
  markdown_upload_returns_message_id?: boolean;
  quick_reply_single_column?: boolean;
  supports_typing_indicator?: boolean;
  typing_indicator_requires_clear?: boolean;
  typing_indicator_best_effort?: boolean;
  supports_reaction_indicator?: boolean;
  supports_message_indicator?: boolean;
  supports_message_indicator_delete?: boolean;
  preferred_processing_indicator?: string;
  force_preferred_processing_indicator?: boolean;
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
    credential_fields: ['bot_token'],
    capabilities: {
      supports_channels: true,
      supports_threads: true,
      supports_buttons: true,
      supports_quick_replies: true,
      supports_message_editing: true,
      supports_typing_indicator: true,
      typing_indicator_best_effort: true,
      supports_reaction_indicator: true,
      supports_message_indicator: true,
      preferred_processing_indicator: 'typing',
    },
  },
  {
    id: 'discord',
    config_key: 'discord',
    title_key: 'platform.discord.title',
    description_key: 'platform.discord.desc',
    credential_fields: ['bot_token'],
    capabilities: {
      supports_channels: true,
      supports_threads: true,
      supports_buttons: true,
      supports_quick_replies: true,
      supports_message_editing: true,
      markdown_upload_returns_message_id: true,
      supports_typing_indicator: true,
      supports_reaction_indicator: true,
      supports_message_indicator: true,
      preferred_processing_indicator: 'typing',
    },
  },
  {
    id: 'telegram',
    config_key: 'telegram',
    title_key: 'platform.telegram.title',
    description_key: 'platform.telegram.desc',
    credential_fields: ['bot_token'],
    capabilities: {
      supports_channels: true,
      supports_threads: false,
      supports_buttons: true,
      supports_quick_replies: true,
      supports_message_editing: true,
      markdown_upload_returns_message_id: true,
      quick_reply_single_column: true,
      supports_typing_indicator: true,
      supports_reaction_indicator: true,
      supports_message_indicator: true,
      supports_message_indicator_delete: true,
      preferred_processing_indicator: 'typing',
    },
  },
  {
    id: 'lark',
    config_key: 'lark',
    title_key: 'platform.lark.title',
    description_key: 'platform.lark.desc',
    credential_fields: ['app_id', 'app_secret'],
    capabilities: {
      supports_channels: true,
      supports_threads: true,
      supports_buttons: true,
      supports_quick_replies: true,
      supports_message_editing: true,
      markdown_upload_returns_message_id: true,
      quick_reply_single_column: true,
      supports_reaction_indicator: true,
      supports_message_indicator: true,
      preferred_processing_indicator: 'reaction',
    },
  },
  {
    id: 'wechat',
    config_key: 'wechat',
    title_key: 'platform.wechat.title',
    description_key: 'platform.wechat.desc',
    credential_fields: ['bot_token'],
    capabilities: {
      supports_channels: false,
      supports_threads: false,
      supports_buttons: false,
      supports_quick_replies: false,
      supports_message_editing: false,
      supports_typing_indicator: true,
      typing_indicator_requires_clear: true,
      supports_reaction_indicator: false,
      supports_message_indicator: true,
      preferred_processing_indicator: 'typing',
      force_preferred_processing_indicator: true,
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

export const platformHasCredentials = (data: any, platform: string): boolean => {
  const descriptor = getPlatformDescriptor(data, platform);
  const configKey = descriptor?.config_key || platform;
  const credentialFields = descriptor?.credential_fields || [];
  const platformConfig = data?.[configKey];
  if (!platformConfig || credentialFields.length === 0) {
    return false;
  }
  return credentialFields.every((field) => !!platformConfig?.[field]);
};

export const hasConfiguredPlatformCredentials = (data: any): boolean =>
  getEnabledPlatforms(data).some((platform) => platformHasCredentials(data, platform));
