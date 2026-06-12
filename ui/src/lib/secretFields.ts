type SecretSection = Record<string, any> | null | undefined;

export const hasConfiguredSecret = (section: SecretSection, field: string): boolean =>
  Boolean(section?.[`has_${field}`]);

export const secretInputValue = (section: SecretSection, field: string): string => {
  const value = section?.[field];
  return typeof value === 'string' ? value : '';
};

export const hasUsableSecret = (section: SecretSection, field: string, draftValue?: string): boolean =>
  Boolean(draftValue) || hasConfiguredSecret(section, field);

export const withSecretDraft = (
  section: SecretSection,
  field: string,
  draftValue: string | null | undefined
): Record<string, any> => {
  const next = { ...(section || {}) };
  const value = typeof draftValue === 'string' ? draftValue.trim() : '';
  if (value) {
    delete next[`has_${field}`];
    delete next[`${field}_length`];
    next[field] = draftValue;
  } else if (hasConfiguredSecret(section, field)) {
    delete next[field];
  } else {
    delete next[`has_${field}`];
    delete next[`${field}_length`];
    next[field] = '';
  }
  return next;
};

export const withSecretDrafts = (
  section: SecretSection,
  drafts: Record<string, string | null | undefined>
): Record<string, any> =>
  Object.entries(drafts).reduce(
    (current, [field, value]) => withSecretDraft(current, field, value),
    { ...(section || {}) }
  );

export const withoutConfiguredSecretMarker = (section: SecretSection, field: string): Record<string, any> => {
  const next = { ...(section || {}) };
  delete next[`has_${field}`];
  delete next[`${field}_length`];
  return next;
};
