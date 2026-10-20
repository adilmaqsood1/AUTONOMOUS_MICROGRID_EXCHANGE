import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/stores/authStore';
import { Leaf, Lock, Mail, Wallet, Bot, ShieldCheck, Zap, ArrowRight } from 'lucide-react';

export const LoginPage: React.FC = () => {
  const navigate = useNavigate();
  const { login } = useAuthStore();

  const [email, setEmail] = useState('operator@greengrid.local');
  const [password, setPassword] = useState('admin123');
  const [rememberMe, setRememberMe] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await login(email, password);
      navigate('/');
    } catch (err: any) {
      setError(err?.message || 'Invalid email or password. Check credentials.');
    } finally {
      setLoading(false);
    }
  };

  const handleDemoLogin = async (roleEmail: string, rolePass: string = 'admin123') => {
    setEmail(roleEmail);
    setPassword(rolePass);
    setLoading(true);
    setError(null);
    try {
      await login(roleEmail, rolePass);
      navigate('/');
    } catch (err: any) {
      setError(err?.message || 'Failed to authenticate demo user.');
    } finally {
      setLoading(false);
    }
  };

  const handleWalletLogin = async () => {
    handleDemoLogin('operator@greengrid.local', 'admin123');
  };

  return (
    <div className="min-h-screen w-screen flex bg-white text-slate-900 font-sans">
      {/* Left Column: Sustainable Microgrid Imagery & Vision matching screenshot */}
      <div className="relative hidden lg:flex lg:w-1/2 flex-col justify-between p-12 overflow-hidden border-r border-slate-200">
        <div
          className="absolute inset-0 bg-cover bg-center brightness-[0.8] contrast-[1.05]"
          style={{ backgroundImage: `url('/login_hero.jpg')` }}
        />
        <div className="absolute inset-0 bg-gradient-to-t from-[#0B1714]/80 via-transparent to-transparent" />

        {/* Top Logo & Title */}
        <div className="relative z-10">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-emerald-500/20 border border-emerald-400/40 backdrop-blur-md flex items-center justify-center text-emerald-400 shadow-lg">
              <Leaf className="w-6 h-6 fill-emerald-400 text-emerald-400" />
            </div>
            <div>
              <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-1.5">
                Green<span className="text-emerald-400">Grid</span>
              </h1>
              <p className="text-xs text-white/90 font-mono tracking-wide drop-shadow">
                Autonomous Transactive Microgrid Energy Exchange
              </p>
            </div>
          </div>
          <p className="mt-4 text-sm text-white/90 font-medium leading-relaxed drop-shadow">
            Clean Energy &bull; Fair Markets &bull; Resilient Communities
          </p>
        </div>

        {/* Bottom Highlights & Badges */}
        <div className="relative z-10 space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-black/40 border border-white/20 backdrop-blur-md text-xs font-semibold text-white">
              <Bot className="w-4 h-4 text-emerald-400" />
              <span>AI-Powered</span>
            </div>
            <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-black/40 border border-white/20 backdrop-blur-md text-xs font-semibold text-white">
              <ShieldCheck className="w-4 h-4 text-cyan-400" />
              <span>Blockchain Secured</span>
            </div>
            <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-black/40 border border-white/20 backdrop-blur-md text-xs font-semibold text-white">
              <Zap className="w-4 h-4 text-amber-400" />
              <span>Real-World Impact</span>
            </div>
          </div>
        </div>
      </div>

      {/* Right Column: Clean White Sign In Form matching Screen 1 in screenshot */}
      <div className="flex-1 flex items-center justify-center p-8 lg:p-16 bg-white">
        <div className="w-full max-w-md space-y-6">
          <div className="text-left space-y-1">
            <h2 className="text-2xl font-bold tracking-tight text-slate-900">Welcome Back</h2>
            <p className="text-sm text-slate-500">Sign in to your account</p>
          </div>

          {error && (
            <div className="p-3 bg-rose-50 border border-rose-200 rounded-lg text-xs text-rose-700 font-medium">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-slate-700">Email address</label>
              <div className="relative">
                <Mail className="w-4 h-4 text-slate-400 absolute left-3 top-3 pointer-events-none" />
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  required
                  className="w-full pl-9 pr-3 py-2.5 rounded-lg bg-white border border-slate-300 text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:border-blue-600 focus:ring-1 focus:ring-blue-600 transition-colors"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-slate-700">Password</label>
              <div className="relative">
                <Lock className="w-4 h-4 text-slate-400 absolute left-3 top-3 pointer-events-none" />
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  required
                  className="w-full pl-9 pr-3 py-2.5 rounded-lg bg-white border border-slate-300 text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:border-blue-600 focus:ring-1 focus:ring-blue-600 transition-colors"
                />
              </div>
            </div>

            <div className="flex items-center justify-between text-xs">
              <label className="flex items-center gap-2 cursor-pointer text-slate-600 hover:text-slate-800">
                <input
                  type="checkbox"
                  checked={rememberMe}
                  onChange={(e) => setRememberMe(e.target.checked)}
                  className="rounded border-slate-300 text-blue-600 focus:ring-0"
                />
                <span>Remember me</span>
              </label>
              <a href="#forgot" className="text-blue-600 hover:underline font-medium">
                Forgot password?
              </a>
            </div>

            {/* Bright Royal Blue Sign In Button matching screenshot */}
            <button
              type="submit"
              disabled={loading}
              className="w-full py-2.5 px-4 rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-bold text-sm transition-all duration-150 shadow-md shadow-blue-600/20 flex items-center justify-center gap-2 disabled:opacity-50"
            >
              <span>{loading ? 'Authenticating...' : 'Sign In'}</span>
              <ArrowRight className="w-4 h-4" />
            </button>
          </form>

          {/* Divider */}
          <div className="relative flex items-center justify-center">
            <div className="border-t border-slate-200 w-full" />
            <span className="bg-white px-3 text-xs text-slate-400 font-medium uppercase">
              or
            </span>
          </div>

          {/* Continue with External Wallet button */}
          <button
            onClick={handleWalletLogin}
            className="w-full py-2.5 px-4 rounded-lg bg-white hover:bg-slate-50 border border-slate-300 text-slate-700 text-xs font-semibold transition-all duration-150 flex items-center justify-center gap-2 shadow-xs"
          >
            <Wallet className="w-4 h-4 text-blue-600" />
            <span>Continue with External Wallet</span>
          </button>

          {/* Quick Demo Personas Selection */}
          <div className="pt-2">
            <div className="text-[11px] text-slate-400 mb-2 font-mono uppercase tracking-wider">
              Quick Demo Personas
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <button
                type="button"
                onClick={() => handleDemoLogin('operator@greengrid.local', 'admin123')}
                className="p-2.5 rounded-lg bg-slate-50 hover:bg-slate-100 border border-slate-200 text-left transition-colors"
              >
                <div className="font-bold text-slate-800">Operator</div>
                <div className="text-[10px] text-emerald-600 font-mono">operator@greengrid.local</div>
              </button>
              <button
                type="button"
                onClick={() => handleDemoLogin('prosumer1@greengrid.local', 'admin123')}
                className="p-2.5 rounded-lg bg-slate-50 hover:bg-slate-100 border border-slate-200 text-left transition-colors"
              >
                <div className="font-bold text-slate-800">Prosumer Agent</div>
                <div className="text-[10px] text-blue-600 font-mono">prosumer1@greengrid.local</div>
              </button>
            </div>
          </div>

          <div className="text-center text-xs text-slate-500 pt-2">
            New to GreenGrid?{' '}
            <a href="#register" className="text-blue-600 hover:underline font-semibold">
              Create an account
            </a>
          </div>
        </div>
      </div>
    </div>
  );
};
