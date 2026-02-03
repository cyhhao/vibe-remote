import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Languages } from 'lucide-react';
import { useApi } from '../context/ApiContext';

export const LanguageSwitcher: React.FC = () => {
  const { i18n, t } = useTranslation();
  const { getConfig, saveConfig } = useApi();
  const [config, setConfig] = useState<any>(null);

  // Load config and sync language on mount
  useEffect(() => {
    const loadConfig = async () => {
      try {
        const cfg = await getConfig();
        setConfig(cfg);
        // Sync i18n with config language
        if (cfg.language && cfg.language !== i18n.language) {
          i18n.changeLanguage(cfg.language);
        }
      } catch {
        // Ignore errors on config load
      }
    };
    loadConfig();
  }, []);

  const languageCodes = Object.keys(i18n.options.resources ?? {});
  const availableLanguages = languageCodes.length ? languageCodes : ['en'];
  const languages = availableLanguages.map((code) => ({
    code,
    label: t(`language.${code}`, { defaultValue: code }),
  }));
  const currentLang = languages.find((l) => l.code === i18n.language) || languages[0];

  const handleChange = async (e: React.ChangeEvent<HTMLSelectElement>) => {
    const newLang = e.target.value;
    i18n.changeLanguage(newLang);

    // Save to config
    try {
      const baseConfig = config ?? await getConfig();
      const updatedConfig = { ...baseConfig, language: newLang };
      await saveConfig(updatedConfig);
      setConfig(updatedConfig);
    } catch {
      // Ignore save errors - language change already applied locally
    }
  };

  return (
    <div className="flex items-center gap-2">
      <Languages size={16} className="text-muted" />
      <select
        value={currentLang.code}
        onChange={handleChange}
        className="bg-transparent border border-border rounded px-2 py-1 text-sm text-text cursor-pointer hover:bg-neutral-50 focus:outline-none focus:ring-1 focus:ring-accent"
      >
        {languages.map((lang) => (
          <option key={lang.code} value={lang.code}>
            {lang.label}
          </option>
        ))}
      </select>
    </div>
  );
};
