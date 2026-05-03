import React from 'react';
import { useTranslation } from 'react-i18next';

import { DoctorPanel } from '@/components/steps/DoctorPanel';
import { SettingsPageShell } from './SettingsPageShell';

export const SettingsDiagnosticsPage: React.FC = () => {
  const { t } = useTranslation();

  return (
    <SettingsPageShell
      activeTab="diagnostics"
      title={t('settings.diagnosticsTitle')}
      subtitle={t('settings.diagnosticsSubtitle')}
    >
      <DoctorPanel isPage logsPath="/logs" titleKey="settings.diagnosticsDoctorTitle" />
    </SettingsPageShell>
  );
};
