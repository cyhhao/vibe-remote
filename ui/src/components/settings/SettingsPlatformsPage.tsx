import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { PlatformSelection } from '@/components/steps/PlatformSelection';
import { useApi } from '@/context/ApiContext';
import { useToast } from '@/context/ToastContext';
import { SettingsPageShell } from './SettingsPageShell';

export const SettingsPlatformsPage: React.FC = () => {
  const { t } = useTranslation();
  const api = useApi();
  const { showToast } = useToast();
  const [config, setConfig] = useState<any>(null);

  useEffect(() => {
    api.getConfig().then(setConfig).catch(() => {});
  }, [api]);

  const handleSave = async (nextData: any) => {
    await api.saveConfig(nextData);
    setConfig((prev: any) => ({ ...(prev || {}), ...nextData }));
    showToast(t('common.saved'), 'success');
  };

  return (
    <SettingsPageShell
      activeTab="platforms"
      title={t('settings.platformsTitle')}
      subtitle={t('settings.platformsSubtitle')}
    >
      {config ? (
        <PlatformSelection data={config} onNext={() => {}} isPage onSave={handleSave} />
      ) : (
        <div className="text-sm text-muted">{t('common.loading')}</div>
      )}
    </SettingsPageShell>
  );
};
