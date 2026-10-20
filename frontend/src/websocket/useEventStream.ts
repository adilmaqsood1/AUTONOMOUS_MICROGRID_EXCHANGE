import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useMicrogridStore } from '@/stores/microgridStore';

export interface WebSocketEventMessage {
  event: string;
  timestamp: string;
  microgrid_id?: string;
  data: any;
}

export function useEventStream() {
  const queryClient = useQueryClient();
  const { microgridId } = useMicrogridStore();
  const [isConnected, setIsConnected] = useState(false);
  const [latestEvent, setLatestEvent] = useState<WebSocketEventMessage | null>(null);
  const [eventHistory, setEventHistory] = useState<WebSocketEventMessage[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let reconnectTimeout: any;
    let isCancelled = false;

    const connect = () => {
      if (isCancelled) return;

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const host = window.location.host;
      const token = localStorage.getItem('mgx_token');
      const params = new URLSearchParams();
      if (microgridId) params.append('microgrid_id', microgridId);
      if (token) params.append('token', token);
      const queryStr = params.toString() ? `?${params.toString()}` : '';
      const url = `${protocol}//${host}/ws${queryStr}`;

      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setIsConnected(true);
      };

      ws.onmessage = (event) => {
        try {
          const payload: WebSocketEventMessage = JSON.parse(event.data);
          setLatestEvent(payload);
          setEventHistory((prev) => [payload, ...prev].slice(0, 40));

          // Invalidate relevant queries reactively
          switch (payload.event) {
            case 'MICROGRID_STATE':
            case 'ASSET_UPDATE':
            case 'GRID_CONGESTION':
            case 'GRID_OUTAGE':
            case 'ISLAND_MODE':
              queryClient.invalidateQueries({ queryKey: ['controlRoom'] });
              queryClient.invalidateQueries({ queryKey: ['topology'] });
              break;
            case 'ORDER_CREATED':
            case 'MARKET_CLEARED':
            case 'TRADE_CREATED':
              queryClient.invalidateQueries({ queryKey: ['orderBook'] });
              queryClient.invalidateQueries({ queryKey: ['trades'] });
              queryClient.invalidateQueries({ queryKey: ['controlRoom'] });
              break;
            case 'SETTLEMENT':
            case 'CERTIFICATE_EVENT':
              queryClient.invalidateQueries({ queryKey: ['blockchainStatus'] });
              queryClient.invalidateQueries({ queryKey: ['blockchainTransactions'] });
              break;
            case 'AGENT_DECISION':
            case 'AGENT_OUTCOME':
            case 'MARKET_ROUND_COMPLETED':
              queryClient.invalidateQueries({ queryKey: ['agents'] });
              queryClient.invalidateQueries({ queryKey: ['leaderboard'] });
              queryClient.invalidateQueries({ queryKey: ['controlRoom'] });
              break;
            default:
              break;
          }
        } catch {
          // Heartbeat ping/pong response
        }
      };

      ws.onclose = () => {
        setIsConnected(false);
        if (!isCancelled) {
          reconnectTimeout = setTimeout(connect, 3000);
        }
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connect();

    return () => {
      isCancelled = true;
      clearTimeout(reconnectTimeout);
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [microgridId, queryClient]);

  return { isConnected, latestEvent, eventHistory };
}
