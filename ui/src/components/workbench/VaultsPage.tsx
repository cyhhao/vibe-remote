import { KeyRound } from 'lucide-react';
import { WorkbenchModulePlaceholder } from './WorkbenchModulePlaceholder';
import { CapabilityTabs } from './CapabilityTabs';

export const VaultsPage: React.FC = () => (
  <div className="mx-auto flex w-full max-w-[1200px] flex-col gap-5 py-2">
    <CapabilityTabs />
    <WorkbenchModulePlaceholder icon={<KeyRound className="size-6" />} i18nPrefix="workbench.modules.vaults" />
  </div>
);
