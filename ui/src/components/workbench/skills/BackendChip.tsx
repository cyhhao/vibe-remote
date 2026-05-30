import clsx from 'clsx';
import { BACKEND_CHIP, BACKEND_DOT, BACKEND_LABEL, type Backend } from '../../../lib/backendAccent';

/** Small pill marking a backend a skill is linked to. Colour per backend. */
export function BackendChip({ backend }: { backend: Backend }) {
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5',
        BACKEND_CHIP[backend],
      )}
    >
      <span className={clsx('size-[5px] rounded-full', BACKEND_DOT[backend])} />
      <span className="font-mono text-[10px] font-medium">{BACKEND_LABEL[backend]}</span>
    </span>
  );
}
