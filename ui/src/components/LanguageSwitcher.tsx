import React from 'react';
import { useTranslation } from 'react-i18next';
import { Languages } from 'lucide-react';

const languages = [
  { code: 'en', label: 'English' },
  { code: 'zh', label: '中文' },
];

export const LanguageSwitcher: React.FC = () => {
  const { i18n } = useTranslation();

  const currentLang = languages.find((l) => l.code === i18n.language) || languages[0];

  const handleChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    i18n.changeLanguage(e.target.value);
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
