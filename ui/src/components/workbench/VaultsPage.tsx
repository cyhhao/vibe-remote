import { KeyRound } from 'lucide-react';
import { WorkbenchModulePlaceholder } from './WorkbenchModulePlaceholder';

export const VaultsPage: React.FC = () => (
  <WorkbenchModulePlaceholder icon={<KeyRound className="size-6" />} i18nPrefix="workbench.modules.vaults" />
);
