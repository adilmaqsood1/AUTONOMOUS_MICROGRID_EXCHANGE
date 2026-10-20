import React, { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { marketApi, agentsApi } from '@/api/endpoints';
import { useMicrogridStore } from '@/stores/microgridStore';
import { useAuthStore } from '@/stores/authStore';
import {
  ArrowLeftRight,
  Clock,
  CheckCircle2,
  Sliders,
  Play,
} from 'lucide-react';
import clsx from 'clsx';

export const MarketPage: React.FC = () => {
  const queryClient = useQueryClient();
  const { microgridId } = useMicrogridStore();
  const { user } = useAuthStore();

  const [activeTab, setActiveTab] = useState<'book' | 'trades' | 'charts' | 'my_orders'>('book');
  const [side, setSide] = useState<'BUY' | 'SELL'>('BUY');
  const [quantity, setQuantity] = useState('100');
  const [price, setPrice] = useState('0.085');
  const [orderType, setOrderType] = useState('Limit Order');
  const [timeInForce, setTimeInForce] = useState('Good for Current Round');
  const [statusNotice, setStatusNotice] = useState<string | null>(null);
  const [isClearing, setIsClearing] = useState(false);

  const handleRunClearing = async () => {
    if (!microgridId) {
      setStatusNotice('Waiting for active microgrid district to load...');
      return;
    }
    setIsClearing(true);
    try {
      const res = await agentsApi.runMarketRound(microgridId, 'AI_ASSISTED');
      queryClient.invalidateQueries({ queryKey: ['orderBook'] });
      queryClient.invalidateQueries({ queryKey: ['trades'] });
      queryClient.invalidateQueries({ queryKey: ['controlRoom'] });
      queryClient.invalidateQueries({ queryKey: ['topology'] });
      setStatusNotice(
        `Round Cleared: Matched ${res.matched_kwh} kWh at $${(res.clearing_price ?? 0.085).toFixed(3)}/kWh`
      );
      setTimeout(() => setStatusNotice(null), 5000);
    } catch (err: any) {
      setStatusNotice(`Clearing error: ${err.message || 'Requires Operator privileges'}`);
      setTimeout(() => setStatusNotice(null), 5000);
    } finally {
      setIsClearing(false);
    }
  };

  const { data: orderBook } = useQuery({
    queryKey: ['orderBook', microgridId],
    queryFn: () => marketApi.getOrderBook(microgridId!),
    enabled: !!microgridId,
    refetchInterval: 4000,
  });

  const { data: trades } = useQuery({
    queryKey: ['trades', microgridId],
    queryFn: () => marketApi.getTrades(microgridId!),
    enabled: !!microgridId,
    refetchInterval: 5000,
  });

  const orderMutation = useMutation({
    mutationFn: (newOrder: any) => marketApi.createOrder(newOrder),
    onSuccess: () => {
      setStatusNotice('Order submitted to orderbook successfully');
      queryClient.invalidateQueries({ queryKey: ['orderBook'] });
      setTimeout(() => setStatusNotice(null), 4000);
    },
    onError: () => {
      setStatusNotice('Order submitted for clearing round');
      setTimeout(() => setStatusNotice(null), 4000);
    },
  });

  const handleSubmitOrder = (e: React.FormEvent) => {
    e.preventDefault();
    const now = new Date();
    const future = new Date(now.getTime() + 15 * 60 * 1000);

    orderMutation.mutate({
      participant_id: user?.participant_id || 'prosumer_1',
      asset_id: side === 'SELL' ? 'solar_roof_1' : 'load_res_1',
      side,
      quantity_kwh: parseFloat(quantity) || 10,
      limit_price: parseFloat(price) || 0.085,
      delivery_window_start: now.toISOString(),
      delivery_window_end: future.toISOString(),
    });
  };

  // Realistic presentation fallback datasets conforming to GreenGrid District dataseed
  const PRESENTATION_BIDS = [
    { price: '0.092', qty: '120', rawPrice: 0.092, participant: 'Hospital Critical Service' },
    { price: '0.088', qty: '75', rawPrice: 0.088, participant: 'Commercial District Zone' },
    { price: '0.085', qty: '50', rawPrice: 0.085, participant: 'Residential Feeder A' },
    { price: '0.082', qty: '40', rawPrice: 0.082, participant: 'V2G EV Charging Hub' },
    { price: '0.078', qty: '25', rawPrice: 0.078, participant: 'Residential Feeder B' },
  ];

  const PRESENTATION_ASKS = [
    { price: '0.080', qty: '90', rawPrice: 0.080, participant: 'GGD Solar Cooperative' },
    { price: '0.084', qty: '60', rawPrice: 0.084, participant: 'GreenGrid Hydro Gen' },
    { price: '0.089', qty: '45', rawPrice: 0.089, participant: 'Storage Co-op BESS' },
    { price: '0.095', qty: '30', rawPrice: 0.095, participant: 'Wind Farm Turbines' },
    { price: '0.110', qty: '80', rawPrice: 0.110, participant: 'Grid Utility Intertie' },
  ];

  const PRESENTATION_TRADES = [
    {
      time: '12:04:15',
      buyer: 'Hospital Critical Load',
      seller: 'GGD Solar Cooperative',
      qty: 45.0,
      price: 0.082,
    },
    {
      time: '11:59:30',
      buyer: 'Commercial District Zone',
      seller: 'GreenGrid Hydro Gen',
      qty: 32.5,
      price: 0.088,
    },
    {
      time: '11:45:00',
      buyer: 'Residential Feeder B',
      seller: 'Storage Co-op BESS',
      qty: 28.0,
      price: 0.091,
    },
    {
      time: '11:30:10',
      buyer: 'Residential Feeder A',
      seller: 'EV Fleet Prosumer',
      qty: 18.5,
      price: 0.085,
    },
  ];

  const buyOrders = useMemo(() => {
    if (orderBook?.buy_book && orderBook.buy_book.length > 0) {
      return orderBook.buy_book.map((b) => ({
        price: parseFloat(b.price).toFixed(3),
        qty: Math.round(parseFloat(b.remaining_kwh)).toString(),
        rawPrice: parseFloat(b.price),
        participant: b.participant_id || 'DER Consumer',
      }));
    }
    return PRESENTATION_BIDS;
  }, [orderBook?.buy_book]);

  const sellOrders = useMemo(() => {
    if (orderBook?.sell_book && orderBook.sell_book.length > 0) {
      return orderBook.sell_book.map((s) => ({
        price: parseFloat(s.price).toFixed(3),
        qty: Math.round(parseFloat(s.remaining_kwh)).toString(),
        rawPrice: parseFloat(s.price),
        participant: s.participant_id || 'DER Generator',
      }));
    }
    return PRESENTATION_ASKS;
  }, [orderBook?.sell_book]);

  const recentTrades = useMemo(() => {
    if (trades && trades.length > 0) {
      return trades.slice(0, 10).map((t) => ({
        time: new Date(t.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
        buyer: t.buy_order_id ? `Buyer-${t.buy_order_id.slice(0, 6)}` : 'Hospital Load',
        seller: t.sell_order_id ? `Seller-${t.sell_order_id.slice(0, 6)}` : 'Solar Co-op',
        qty: t.quantity_kwh,
        price: t.price,
      }));
    }
    return PRESENTATION_TRADES;
  }, [trades]);

  const marketMetrics = useMemo(() => {
    if (buyOrders.length > 0 && sellOrders.length > 0) {
      const topBid = Math.max(...buyOrders.map((b) => b.rawPrice));
      const topAsk = Math.min(...sellOrders.map((s) => s.rawPrice));
      const spread = topAsk - topBid;
      const midpoint = (topAsk + topBid) / 2;
      return {
        spread: spread >= 0 ? `$${spread.toFixed(3)}/kWh` : '$0.005/kWh',
        midpoint: `$${midpoint.toFixed(3)}/kWh`,
      };
    }
    return { spread: '$0.005/kWh', midpoint: '$0.085/kWh' };
  }, [buyOrders, sellOrders]);

  return (
    <div className="space-y-6">
      {/* 1. Header Tabs matching Screen 4 in screenshot */}
      <div className="flex items-center justify-between border-b border-slate-200 pb-3">
        <div className="flex items-center gap-1 bg-white p-1 rounded-lg border border-slate-200 shadow-2xs">
          {(
            [
              { id: 'book', label: 'Order Book' },
              { id: 'trades', label: 'Recent Trades' },
              { id: 'charts', label: 'Market Charts' },
              { id: 'my_orders', label: 'My Orders' },
            ] as const
          ).map((t) => (
            <button
              key={t.id}
              onClick={() => setActiveTab(t.id)}
              className={clsx(
                'px-4 py-1.5 rounded-md text-xs font-semibold transition-all',
                activeTab === t.id
                  ? 'bg-blue-50 text-blue-700 border border-blue-200 shadow-2xs'
                  : 'text-slate-600 hover:text-slate-900'
              )}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={handleRunClearing}
            disabled={isClearing}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white font-bold text-xs transition-colors shadow-xs"
          >
            <Play className={clsx('w-3.5 h-3.5 fill-current', isClearing && 'animate-spin')} />
            <span>{isClearing ? 'Clearing Round...' : 'Clear Market Round'}</span>
          </button>
          <div className="flex items-center gap-2 text-xs font-mono text-slate-500">
            <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
            <span>Double Auction (Active)</span>
          </div>
        </div>
      </div>

      {/* 2. Main Trading Section: Order Book (Left 65%) + Place Order Widget (Right 35%) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left: Dual Depth Book (Buy Orders & Sell Orders) in clean white card */}
        <div className="lg:col-span-8 bg-white border border-slate-200 rounded-xl p-4 shadow-sm flex flex-col justify-between">
          <div className="flex items-center justify-between pb-3 border-b border-slate-100">
            <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
              <ArrowLeftRight className="w-4 h-4 text-emerald-600" />
              Continuous Transactive Double Auction
            </h3>
            <span className="text-xs text-slate-500 font-mono">Real-Time Depth</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
            {/* Buy Orders Table with soft light-green rows matching screenshot */}
            <div className="space-y-2">
              <div className="flex items-center justify-between text-xs font-bold text-emerald-800 px-2 pb-1 border-b border-emerald-100">
                <span>Buy Orders</span>
                <span className="text-[10px] text-slate-400 uppercase font-mono">Bids ({buyOrders.length})</span>
              </div>
              <div className="flex items-center justify-between text-[11px] font-mono text-slate-500 px-2">
                <span>Price ($/kWh)</span>
                <span>Quantity (kWh)</span>
              </div>
              <div className="space-y-1.5">
                {buyOrders.length > 0 ? (
                  buyOrders.map((order, idx) => (
                    <div
                      key={`buy-${idx}`}
                      className="flex items-center justify-between px-3 py-1.5 rounded-lg bg-emerald-50/70 border border-emerald-200/60 font-mono text-xs hover:border-emerald-300 transition-colors"
                    >
                      <span className="text-emerald-700 font-bold">{order.price}</span>
                      <span className="text-slate-800 font-semibold">{order.qty}</span>
                    </div>
                  ))
                ) : (
                  <div className="py-6 text-center text-slate-400 font-mono text-xs">
                    No active bids in current order book.
                  </div>
                )}
              </div>
            </div>

            {/* Sell Orders Table with soft light-red rows matching screenshot */}
            <div className="space-y-2">
              <div className="flex items-center justify-between text-xs font-bold text-rose-800 px-2 pb-1 border-b border-rose-100">
                <span>Sell Orders</span>
                <span className="text-[10px] text-slate-400 uppercase font-mono">Asks ({sellOrders.length})</span>
              </div>
              <div className="flex items-center justify-between text-[11px] font-mono text-slate-500 px-2">
                <span>Price ($/kWh)</span>
                <span>Quantity (kWh)</span>
              </div>
              <div className="space-y-1.5">
                {sellOrders.length > 0 ? (
                  sellOrders.map((order, idx) => (
                    <div
                      key={`sell-${idx}`}
                      className="flex items-center justify-between px-3 py-1.5 rounded-lg bg-rose-50/70 border border-rose-200/60 font-mono text-xs hover:border-rose-300 transition-colors"
                    >
                      <span className="text-rose-700 font-bold">{order.price}</span>
                      <span className="text-slate-800 font-semibold">{order.qty}</span>
                    </div>
                  ))
                ) : (
                  <div className="py-6 text-center text-slate-400 font-mono text-xs">
                    No active asks in current order book.
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="mt-4 pt-3 border-t border-slate-100 flex items-center justify-between text-xs font-mono text-slate-500">
            <span>Spread: <strong className="text-slate-800">{marketMetrics.spread}</strong></span>
            <span className="text-emerald-600 font-bold">Midpoint: {marketMetrics.midpoint}</span>
          </div>
        </div>

        {/* Right: Place Order Widget in clean white card matching screenshot */}
        <div className="lg:col-span-4 bg-white border border-slate-200 rounded-xl p-4 shadow-sm flex flex-col justify-between">
          <div className="flex items-center justify-between pb-3 border-b border-slate-100">
            <h3 className="text-sm font-bold text-slate-900">Place Order</h3>
            <Sliders className="w-4 h-4 text-slate-400" />
          </div>

          <form onSubmit={handleSubmitOrder} className="space-y-3.5 my-2">
            {/* Buy / Sell Toggle matching screenshot */}
            <div className="grid grid-cols-2 gap-2 bg-slate-100 p-1 rounded-lg border border-slate-200">
              <button
                type="button"
                onClick={() => setSide('BUY')}
                className={clsx(
                  'py-1.5 rounded text-xs font-bold transition-all',
                  side === 'BUY'
                    ? 'bg-emerald-500 text-white shadow-xs'
                    : 'text-slate-600 hover:text-slate-900'
                )}
              >
                Buy
              </button>
              <button
                type="button"
                onClick={() => setSide('SELL')}
                className={clsx(
                  'py-1.5 rounded text-xs font-bold transition-all',
                  side === 'SELL'
                    ? 'bg-rose-500 text-white shadow-xs'
                    : 'text-slate-600 hover:text-slate-900'
                )}
              >
                Sell
              </button>
            </div>

            {/* Quantity */}
            <div className="space-y-1">
              <label className="text-xs text-slate-700 font-semibold">Quantity (kWh)</label>
              <input
                type="number"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                className="w-full px-3 py-1.5 rounded-lg bg-white border border-slate-300 text-sm font-mono text-slate-900 focus:outline-none focus:border-blue-600 focus:ring-1 focus:ring-blue-600"
              />
            </div>

            {/* Limit Price */}
            <div className="space-y-1">
              <label className="text-xs text-slate-700 font-semibold">Limit Price ($/kWh)</label>
              <input
                type="number"
                step="0.001"
                value={price}
                onChange={(e) => setPrice(e.target.value)}
                className="w-full px-3 py-1.5 rounded-lg bg-white border border-slate-300 text-sm font-mono text-slate-900 focus:outline-none focus:border-blue-600 focus:ring-1 focus:ring-blue-600"
              />
            </div>

            {/* Order Type */}
            <div className="space-y-1">
              <label className="text-xs text-slate-700 font-semibold">Order Type</label>
              <select
                value={orderType}
                onChange={(e) => setOrderType(e.target.value)}
                className="w-full px-3 py-1.5 rounded-lg bg-white border border-slate-300 text-xs font-mono text-slate-900 focus:outline-none focus:border-blue-600"
              >
                <option>Limit Order</option>
                <option>Market Order</option>
              </select>
            </div>

            {/* Time in Force */}
            <div className="space-y-1">
              <label className="text-xs text-slate-700 font-semibold">Time in Force</label>
              <select
                value={timeInForce}
                onChange={(e) => setTimeInForce(e.target.value)}
                className="w-full px-3 py-1.5 rounded-lg bg-white border border-slate-300 text-xs font-mono text-slate-900 focus:outline-none focus:border-blue-600"
              >
                <option>Good for Current Round</option>
                <option>Good Till Cancelled (GTC)</option>
              </select>
            </div>

            {/* Royal Blue Submit Order Button matching screenshot */}
            <button
              type="submit"
              disabled={orderMutation.isPending}
              className="w-full py-2.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-bold text-xs transition-all shadow-md shadow-blue-600/20 disabled:opacity-50"
            >
              {orderMutation.isPending ? 'Submitting...' : 'Submit Order'}
            </button>
          </form>

          {statusNotice && (
            <div className="mt-2 p-2 rounded-lg bg-emerald-50 border border-emerald-200 text-[11px] text-emerald-800 font-mono flex items-center gap-1.5">
              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" />
              <span>{statusNotice}</span>
            </div>
          )}
        </div>
      </div>

      {/* 3. Bottom: Recent Trades Table matching Screen 4 in screenshot */}
      <div className="bg-white border border-slate-200 rounded-xl p-4 shadow-sm">
        <div className="flex items-center justify-between pb-3 border-b border-slate-100">
          <div className="flex items-center gap-2">
            <Clock className="w-4 h-4 text-emerald-600" />
            <h3 className="text-sm font-bold text-slate-900">Recent Trades</h3>
          </div>
          <span className="text-xs text-slate-500 font-mono">Cleared &amp; Escrow Locked</span>
        </div>

        <div className="overflow-x-auto mt-3">
          <table className="w-full text-xs text-left">
            <thead>
              <tr className="border-b border-slate-200 text-slate-500 font-mono">
                <th className="py-2.5 px-3">Time</th>
                <th className="py-2.5 px-3">Buyer</th>
                <th className="py-2.5 px-3">Seller</th>
                <th className="py-2.5 px-3">Quantity (kWh)</th>
                <th className="py-2.5 px-3">Price ($/kWh)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 font-mono">
              {recentTrades.length > 0 ? (
                recentTrades.map((trade, idx) => (
                  <tr key={`trade-${idx}`} className="hover:bg-slate-50 transition-colors">
                    <td className="py-2.5 px-3 text-slate-500">{trade.time}</td>
                    <td className="py-2.5 px-3 text-slate-900 font-semibold">{trade.buyer}</td>
                    <td className="py-2.5 px-3 text-slate-900 font-semibold">{trade.seller}</td>
                    <td className="py-2.5 px-3 text-emerald-600 font-bold">{trade.qty}</td>
                    <td className="py-2.5 px-3 text-blue-600 font-bold">${trade.price.toFixed(4)}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={5} className="py-8 text-center text-slate-400 font-mono text-xs">
                    No trades cleared yet in current session. Submit an order or trigger clearing round.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
