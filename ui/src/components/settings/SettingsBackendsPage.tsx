import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { AgentDetection } from '@/components/steps/AgentDetection';
import { useApi } from '@/context/ApiContext';
import { useToast } from '@/context/ToastContext';
import { SettingsPageShell } from './SettingsPageShell';

export const SettingsBackendsPage: React.FC = () => {
  const { t } = useTranslation();
  const api = useApi();
  const { showToast } = useToast();
  const [config, setConfig] = useState<any>(null);

  useEffect(() => {
    api.getConfig().then(setConfig).catch(() => {});
  }, [api]);

  const handleSave = async (nextData: { agents: any; default_backend: string }) => {
    await api.saveConfig({ agents: { ...nextData.agents, default_backend: nextData.default_backend } });
    setConfig((prev: any) => ({ ...(prev || {}), agents: { ...nextData.agents, default_backend: nextData.default_backend } }));
    showToast(t('common.saved'), 'success');
  };

  return (
    <SettingsPageShell
      activeTab="backends"
      title={t('settings.backendsTitle')}
      subtitle={t('settings.backendsSubtitle')}
    >
      {config ? (
        <AgentDetection data={config} onNext={() => {}} isPage onSave={handleSave} />
      ) : (
        <div className="text-sm text-muted">{t('common.loading')}</div>
      )}
    </SettingsPageShell>
  );
};
