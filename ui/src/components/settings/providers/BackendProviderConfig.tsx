import type { BackendId } from '../shared/useBackendRuntime';
import { ClaudeProviderConfig } from './ClaudeProviderConfig';
import { CodexProviderConfig } from './CodexProviderConfig';
import { OpencodeProviderConfig } from './OpencodeProviderConfig';

/**
 * Dispatches to the backend-specific config body. The same component
 * tree is rendered by the settings route (wrapped in ``SettingsPageShell``)
 * and the setup wizard modal, so neither callsite copies the per-backend
 * logic.
 */
export function BackendProviderConfig({ backend }: { backend: BackendId }) {
  switch (backend) {
    case 'claude':
      return <ClaudeProviderConfig />;
    case 'codex':
      return <CodexProviderConfig />;
    case 'opencode':
      return <OpencodeProviderConfig />;
    default:
      return null;
  }
}
