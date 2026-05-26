import { Inbox } from 'lucide-react';
import { WorkbenchModulePlaceholder } from './WorkbenchModulePlaceholder';

export const InboxPage: React.FC = () => (
  <WorkbenchModulePlaceholder icon={<Inbox className="size-6" />} i18nPrefix="workbench.modules.inbox" />
);
