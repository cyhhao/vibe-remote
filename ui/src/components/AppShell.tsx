import React from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { LayoutDashboard, MessageSquare, Activity } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useStatus } from '../context/StatusContext';
import { LanguageSwitcher } from './LanguageSwitcher';
import clsx from 'clsx';
import logoImg from '../assets/logo.png';

const NavItem = ({ to, icon: Icon, children }: { to: string; icon: any; children: React.ReactNode }) => (
  <NavLink
    to={to}
    className={({ isActive }) =>
      clsx(
        'flex items-center gap-3 px-3 py-2 rounded-md transition-colors',
        isActive ? 'bg-accent/10 text-accent font-medium' : 'text-muted hover:bg-neutral-100 hover:text-text'
      )
    }
  >
    <Icon className="w-5 h-5" />
    <span>{children}</span>
  </NavLink>
);

export const AppShell: React.FC = () => {
  const { t } = useTranslation();
  const { status } = useStatus();
  const location = useLocation();

  const isRunning = status.state === 'running';

  if (location.pathname === '/setup') {
    return <Outlet />;
  }

  return (
    <div className="min-h-screen flex bg-bg text-text font-sans">
      {/* Sidebar */}
      <aside className="w-64 border-r border-border bg-panel hidden md:flex flex-col">
        <div className="p-6 border-b border-border">
            <h1 className="text-xl font-bold font-display tracking-tight flex items-center gap-2">
                <img src={logoImg} alt="Vibe Remote Logo" className="w-6 h-6 rounded-md" />
                {t('appShell.title')}
            </h1>
        </div>

        <nav className="flex-1 p-4 space-y-1">
          <NavItem to="/dashboard" icon={LayoutDashboard}>{t('nav.dashboard')}</NavItem>
          <NavItem to="/channels" icon={MessageSquare}>{t('nav.channels')}</NavItem>
          <NavItem to="/doctor" icon={Activity}>{t('nav.doctor')}</NavItem>
        </nav>

        <div className="p-4 border-t border-border space-y-3">
             <LanguageSwitcher />
             <div className="flex items-center justify-between bg-neutral-50 p-3 rounded-lg border border-border">
                <div className="flex items-center gap-2">
                    <div className={clsx("w-2.5 h-2.5 rounded-full", isRunning ? "bg-success" : "bg-muted")}></div>
                    <span className="text-sm font-medium">{isRunning ? t('common.running') : t('common.stopped')}</span>
                </div>
             </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 overflow-auto p-4 md:p-8">
        <Outlet />
      </main>

       {/* Mobile Nav */}
       <nav className="md:hidden fixed bottom-0 left-0 right-0 bg-panel border-t border-border flex justify-around p-3 z-50 pb-safe">
          <NavLink to="/dashboard" className={({isActive}) => clsx("p-2 rounded-lg", isActive ? "text-accent" : "text-muted")}><LayoutDashboard /></NavLink>
          <NavLink to="/channels" className={({isActive}) => clsx("p-2 rounded-lg", isActive ? "text-accent" : "text-muted")}><MessageSquare /></NavLink>
          <NavLink to="/doctor" className={({isActive}) => clsx("p-2 rounded-lg", isActive ? "text-accent" : "text-muted")}><Activity /></NavLink>
       </nav>
    </div>
  );
};
