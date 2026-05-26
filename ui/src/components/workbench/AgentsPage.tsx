import { Bot } from 'lucide-react';
import { WorkbenchModulePlaceholder } from './WorkbenchModulePlaceholder';

export const AgentsPage: React.FC = () => (
  <WorkbenchModulePlaceholder icon={<Bot className="size-6" />} i18nPrefix="workbench.modules.agents" />
);
