import { WandSparkles } from 'lucide-react';
import { WorkbenchModulePlaceholder } from './WorkbenchModulePlaceholder';

export const SkillsPage: React.FC = () => (
  <WorkbenchModulePlaceholder icon={<WandSparkles className="size-6" />} i18nPrefix="workbench.modules.skills" />
);
