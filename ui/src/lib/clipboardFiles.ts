// Pull file blobs out of a clipboard paste, shared by every composer input that
// supports paste-to-upload. A pasted screenshot rides in ``items`` as a ``file``
// entry (and usually in ``files`` too); a file copied in the OS file manager
// lands in ``files`` directly. A plain text / rich-text paste carries no files
// and returns ``[]`` so the editor handles it as normal text.
//
// Pasted screenshots can arrive without a filename, so synthesize a readable,
// collision-free one: the timestamp separates one paste from the next, the index
// separates files staged within the same paste (same millisecond). The upload
// path keys files by their unique on-disk path, so the synthesized name is only
// ever a display label.
export function filesFromClipboard(data: DataTransfer | null): File[] {
  if (!data) return [];
  const direct = Array.from(data.files ?? []);
  const files =
    direct.length > 0
      ? direct
      : Array.from(data.items ?? [])
          .filter((it) => it.kind === 'file')
          .map((it) => it.getAsFile())
          .filter((f): f is File => f != null);
  return files.map((file, i) => {
    if (file.name) return file;
    const ext = file.type.split('/')[1] || 'dat';
    const base = file.type.startsWith('image/') ? 'pasted-image' : 'pasted-file';
    return new File([file], `${base}-${Date.now().toString(36)}-${i}.${ext}`, { type: file.type });
  });
}
