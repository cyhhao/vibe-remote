import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { ArrowRight, SlidersHorizontal } from 'lucide-react';

import { Button } from './ui/button';

// Commit 01 placeholder: the full Workbench shell (capability modules,
// projects, inbox, canvas) is built in later commits. This stub keeps `/`
// routable while the new shell is staged in.
export const Workbench: React.FC = () => {
  const { t } = useTranslation();

  return (
    <div className="mx-auto flex min-h-[calc(100vh-200px)] max-w-2xl flex-col items-center justify-center gap-5 text-center">
      <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-mint">
        {t('workbench.eyebrow')}
      </div>
      <h1 className="text-3xl font-bold text-foreground">{t('workbench.placeholderTitle')}</h1>
      <p className="text-sm leading-relaxed text-muted">{t('workbench.placeholderBody')}</p>
      <Button asChild variant="outline" size="sm">
        <Link to="/admin/dashboard" className="inline-flex items-center gap-2">
          <SlidersHorizontal className="size-3.5" />
          {t('workbench.openControlPanel')}
          <ArrowRight className="size-3.5" />
        </Link>
      </Button>
    </div>
  );
};
