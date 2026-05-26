import { Activity } from 'lucide-react';
import { WorkbenchModulePlaceholder } from './WorkbenchModulePlaceholder';

export const HarnessPage: React.FC = () => (
  <WorkbenchModulePlaceholder icon={<Activity className="size-6" />} i18nPrefix="workbench.modules.harness" />
);
