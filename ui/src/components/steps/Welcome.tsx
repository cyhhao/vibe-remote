import React from 'react';
import { ArrowRight } from 'lucide-react';
import logo from '../../assets/logo.png';

interface WelcomeProps {
  onNext: (data: any) => void;
}

export const Welcome: React.FC<WelcomeProps> = ({ onNext }) => {
  return (
    <div className="flex flex-col h-full justify-center items-center text-center max-w-lg mx-auto">
      <div className="mb-8">
        <img src={logo} alt="Vibe Remote Logo" className="w-20 h-20 mx-auto mb-6 drop-shadow-lg" />
        <h1 className="text-4xl font-display font-bold mb-4 text-text">Set up Vibe Remote</h1>
        <p className="text-xl text-muted font-light">
          Your local AI coding assistant for Slack.
        </p>
      </div>

      <div className="space-y-4 text-left w-full mb-10 bg-panel border border-border p-6 rounded-xl shadow-sm">
        <div className="flex items-start gap-3">
            <div className="w-1.5 h-1.5 rounded-full bg-accent mt-2"></div>
            <p className="text-text">Runs locally. Your code stays on your computer.</p>
        </div>
        <div className="flex items-start gap-3">
             <div className="w-1.5 h-1.5 rounded-full bg-accent mt-2"></div>
             <p className="text-text">SaaS mode uses a cloud relay for Slack delivery, but does not store message content.</p>
        </div>
        <div className="flex items-start gap-3">
             <div className="w-1.5 h-1.5 rounded-full bg-accent mt-2"></div>
             <p className="text-text">Self-host mode runs entirely locally via Slack Socket Mode.</p>
        </div>
      </div>

      <button
        onClick={() => onNext({})}
        className="group flex items-center gap-2 px-8 py-3 bg-accent hover:bg-accent/90 text-white rounded-full font-bold text-lg transition-all shadow-md hover:shadow-lg hover:-translate-y-0.5"
      >
        Get started <ArrowRight size={20} className="group-hover:translate-x-1 transition-transform" />
      </button>
    </div>
  );
};
