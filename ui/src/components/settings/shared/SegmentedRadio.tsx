// Promoted to `ui/segmented.tsx` (now used outside Settings, by the Workbench
// Skills page). This thin re-export keeps existing Settings imports working;
// new callers should import from `@/components/ui/segmented` directly.
export { SegmentedRadio } from '../../ui/segmented';
export type { SegmentedRadioOption, SegmentedRadioProps } from '../../ui/segmented';
