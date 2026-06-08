import { useCallback, useEffect, useRef, useState } from 'react';

export interface FileDropHandlers {
  onDragEnter: (e: React.DragEvent) => void;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: (e: React.DragEvent) => void;
  onDrop: (e: React.DragEvent) => void;
}

export interface UseFileDropResult {
  /** True while a file drag is hovering the zone — drive a drop overlay off this. */
  dragging: boolean;
  /** Spread onto the drop-target element. */
  handlers: FileDropHandlers;
}

// Whether a drag carries files (vs. selected text, a link, an in-page element).
// File contents are only readable on ``drop`` for security, but the format list
// in ``types`` is visible during enter/over/leave — enough to gate the overlay.
function dragHasFiles(e: React.DragEvent): boolean {
  return Array.from(e.dataTransfer?.types ?? []).includes('Files');
}

/**
 * Drag-and-drop file capture for a single drop zone. Returns a ``dragging`` flag
 * (true while a file drag hovers the zone) plus the handlers to spread onto the
 * target element; ``onFiles`` fires once per drop with every dropped file.
 *
 * Tracks a depth counter rather than a bare boolean so dragging across the
 * zone's children doesn't flicker the flag — each child fires its own
 * enter/leave as the cursor crosses it, but the count only returns to zero when
 * the drag truly leaves the zone. Only file drags arm the zone, and ``disabled``
 * makes every handler a no-op (e.g. a text-only composer with no upload target).
 */
export function useFileDrop(
  onFiles: (files: File[]) => void,
  options?: { disabled?: boolean },
): UseFileDropResult {
  const disabled = options?.disabled ?? false;
  const [dragging, setDragging] = useState(false);
  const depth = useRef(0);

  // Safety net for a drag that ends without a balancing dragleave on the zone —
  // dropped outside it, or cancelled with ESC (OS file drags fire no dragend on
  // an in-page source). Reset on any window-level drop/dragend so the overlay
  // can't stick. While disabled the handlers are no-ops (dragging never arms),
  // so there's nothing to listen for or reset.
  useEffect(() => {
    if (disabled) return;
    const reset = () => {
      depth.current = 0;
      setDragging(false);
    };
    window.addEventListener('drop', reset);
    window.addEventListener('dragend', reset);
    return () => {
      window.removeEventListener('drop', reset);
      window.removeEventListener('dragend', reset);
    };
  }, [disabled]);

  const onDragEnter = useCallback(
    (e: React.DragEvent) => {
      if (disabled || !dragHasFiles(e)) return;
      e.preventDefault();
      depth.current += 1;
      setDragging(true);
    },
    [disabled],
  );

  const onDragOver = useCallback(
    (e: React.DragEvent) => {
      if (disabled || !dragHasFiles(e)) return;
      // Required: without preventDefault the element rejects the drop and the
      // browser opens the file instead.
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
    },
    [disabled],
  );

  const onDragLeave = useCallback(
    (e: React.DragEvent) => {
      if (disabled || !dragHasFiles(e)) return;
      e.preventDefault();
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setDragging(false);
    },
    [disabled],
  );

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      if (disabled) return;
      e.preventDefault();
      depth.current = 0;
      setDragging(false);
      const files = Array.from(e.dataTransfer?.files ?? []);
      if (files.length) onFiles(files);
    },
    [disabled, onFiles],
  );

  return { dragging, handlers: { onDragEnter, onDragOver, onDragLeave, onDrop } };
}
