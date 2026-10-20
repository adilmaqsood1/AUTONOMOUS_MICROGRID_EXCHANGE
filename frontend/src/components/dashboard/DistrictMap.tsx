import React, { useEffect, useRef, useState, useMemo } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { Layers, MapPin, Maximize2, Zap, HeartPulse, Building2, Home, Wind, Battery, Radio } from 'lucide-react';
import clsx from 'clsx';
import { LiveControlRoomTelemetry, GridTopology } from '@/types';

interface DistrictMapProps {
  telemetry?: LiveControlRoomTelemetry | null;
  topology?: GridTopology | null;
  selectedAssetId?: string | null;
  onSelectAsset?: (id: string | null) => void;
  className?: string;
  heightClass?: string;
}

interface DistrictNode {
  id: string;
  name: string;
  type: string;
  lat: number;
  lng: number;
  voltage: string;
  kw: number;
  isGen: boolean;
  color: string;
}

interface RenderedFeeder {
  id: string;
  from: string;
  to: string;
  capacityKw: number;
  flowKw: number;
  loadingPct: number;
  isCongested: boolean;
}

const PRESET_LOCATIONS: Record<string, { name: string; center: [number, number] }> = {
  auto: { name: 'Backend Auto-Detect', center: [31.5204, 74.3587] },
  lahore: { name: 'Lahore, Pakistan', center: [31.5204, 74.3587] },
  sf: { name: 'San Francisco, CA', center: [37.7656, -122.4225] },
  london: { name: 'London, UK', center: [51.5074, -0.1278] },
  newyork: { name: 'New York, NY', center: [40.7128, -74.0060] },
};

export const DistrictMap: React.FC<DistrictMapProps> = ({
  telemetry,
  topology,
  selectedAssetId,
  onSelectAsset,
  className,
  heightClass = 'h-[440px]',
}) => {
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapInstanceRef = useRef<L.Map | null>(null);
  const layersRef = useRef<{ [key: string]: L.Layer }>({});
  const polylinesRef = useRef<L.Polyline[]>([]);

  const [mapStyle, setMapStyle] = useState<'streets' | 'dark' | 'satellite'>('streets');
  const [activeInspector, setActiveInspector] = useState<DistrictNode | null>(null);
  const [selectedLocationKey, setSelectedLocationKey] = useState<string>('auto');

  const CARTO_API_KEY =
    (import.meta as any).env?.VITE_CARTO_API_KEY || 'cb1_4e0u_1_3f1dda6192ea8594ef863550';

  // Dynamic Geographic Center resolved from backend topology location
  const backendCenter: [number, number] = useMemo(() => {
    if (topology?.location?.latitude && topology?.location?.longitude) {
      return [topology.location.latitude, topology.location.longitude];
    }
    return [31.5204, 74.3587]; // Default to seeded Lahore coordinates
  }, [topology?.location?.latitude, topology?.location?.longitude]);

  const activeCenter: [number, number] = useMemo(() => {
    if (selectedLocationKey !== 'auto' && PRESET_LOCATIONS[selectedLocationKey]) {
      return PRESET_LOCATIONS[selectedLocationKey].center;
    }
    return backendCenter;
  }, [selectedLocationKey, backendCenter]);

  // Dynamic Geographic district nodes enriched with backend data and location
  const districtNodes: DistrictNode[] = useMemo(() => {
    if (!topology?.nodes || topology.nodes.length === 0) {
      return [];
    }

    const centerLat = activeCenter[0];
    const centerLng = activeCenter[1];

    const offsets: Record<string, [number, number]> = {
      SUBSTATION: [0.0, 0.0],
      WIND_FARM: [0.0050, 0.0],
      HYDRO: [0.0040, 0.0055],
      GRID_TIE: [0.0035, -0.0055],
      HOSPITAL: [0.0032, -0.0123],
      STORAGE: [-0.0005, -0.0060],
      RES_B: [-0.0045, 0.0045],
      RES_A: [-0.0048, -0.0005],
      COMMERCIAL: [-0.0042, -0.0050],
    };

    return topology.nodes.map((n, idx) => {
      const vLevel = n.voltage_level ?? n.voltage_kv ?? n.nominal_voltage_kv ?? 0.4;
      const liveKw =
        n.name === 'GRID_TIE'
          ? Math.round(telemetry?.grid_import_kw ?? n.rated_kw ?? 0)
          : n.rated_kw ?? 0;

      let lat = n.latitude;
      let lng = n.longitude;

      if (selectedLocationKey !== 'auto' || !lat || !lng) {
        const off = offsets[n.name] || [
          0.0045 * Math.cos((idx / topology.nodes.length) * 2 * Math.PI - Math.PI / 2),
          0.0055 * Math.sin((idx / topology.nodes.length) * 2 * Math.PI - Math.PI / 2),
        ];
        lat = Number((centerLat + off[0]).toFixed(5));
        lng = Number((centerLng + off[1]).toFixed(5));
      }

      return {
        id: n.name,
        name: n.name.replace(/_/g, ' '),
        type: n.node_type || 'Grid Bus',
        lat,
        lng,
        voltage: `${vLevel} kV`,
        kw: liveKw,
        isGen: n.node_type === 'GENERATION' || n.node_type === 'STORAGE',
        color: n.color || '#3B82F6',
      };
    });
  }, [topology?.nodes, telemetry?.grid_import_kw, activeCenter, selectedLocationKey]);

  // Dynamic Feeder lines connecting district from backend topology & telemetry
  const districtFeeders: RenderedFeeder[] = useMemo(() => {
    if (!topology?.lines || topology.lines.length === 0) {
      return [];
    }

    const nodeNameById = new Map<string, string>();
    topology.nodes?.forEach((n) => {
      nodeNameById.set(n.id, n.name);
      nodeNameById.set(n.name, n.name);
    });

    return topology.lines.map((l, idx) => {
      const from = nodeNameById.get(l.from_node_id) || l.from_node_id;
      const to = nodeNameById.get(l.to_node_id) || l.to_node_id;
      const fwd = `${from} -> ${to}`;
      const rev = `${to} -> ${from}`;

      const loadingPct =
        telemetry?.line_loadings_percent?.[fwd] ??
        telemetry?.line_loadings_percent?.[rev] ??
        telemetry?.line_loadings_percent?.[l.id] ??
        l.loading_percent ??
        (l.capacity_kw > 0 && l.flow_kw ? Math.round((l.flow_kw / l.capacity_kw) * 100) : 0);

      const flowKw = l.flow_kw || (loadingPct > 0 ? (loadingPct / 100) * l.capacity_kw : 0);
      const isCongested = l.congested || loadingPct >= 90 || l.capacity_kw <= 5.0;

      return {
        id: l.id || `line-${idx}`,
        from,
        to,
        capacityKw: l.capacity_kw,
        flowKw,
        loadingPct,
        isCongested,
      };
    });
  }, [topology?.lines, topology?.nodes, telemetry?.line_loadings_percent]);

  // Initialize Map
  useEffect(() => {
    if (!mapContainerRef.current) return;
    if (mapInstanceRef.current) return;

    // Centered dynamically at microgrid location
    const map = L.map(mapContainerRef.current, {
      center: activeCenter,
      zoom: 16,
      minZoom: 13,
      maxZoom: 18,
      zoomControl: false,
      attributionControl: false,
    });

    // Add zoom controls to top-right
    L.control.zoom({ position: 'topright' }).addTo(map);

    mapInstanceRef.current = map;

    return () => {
      map.remove();
      mapInstanceRef.current = null;
    };
  }, []);

  // Animate map when active center changes
  useEffect(() => {
    if (mapInstanceRef.current) {
      mapInstanceRef.current.flyTo(activeCenter, 16, { duration: 1.2 });
    }
  }, [activeCenter]);

  // Update Tile Layer
  useEffect(() => {
    const map = mapInstanceRef.current;
    if (!map) return;

    if (layersRef.current['tile']) {
      map.removeLayer(layersRef.current['tile']);
    }

    const cartoKeyParam = CARTO_API_KEY ? `?key=${CARTO_API_KEY}` : '';
    let tileUrl = '';

    if (mapStyle === 'streets') {
      tileUrl = `https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png${cartoKeyParam}`;
    } else if (mapStyle === 'dark') {
      tileUrl = `https://basemaps.cartocdn.com/rastertiles/dark_all/{z}/{x}/{y}.png${cartoKeyParam}`;
    } else {
      tileUrl = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
    }

    const tileLayer = L.tileLayer(tileUrl, {
      maxZoom: 19,
      subdomains: 'abcd',
      attribution: '&copy; CARTO &copy; OpenStreetMap contributors',
    }).addTo(map);

    layersRef.current['tile'] = tileLayer;
  }, [mapStyle, CARTO_API_KEY]);

  // Render Feeder Lines & Markers
  useEffect(() => {
    const map = mapInstanceRef.current;
    if (!map) return;

    // Clear old polylines
    polylinesRef.current.forEach((pl) => map.removeLayer(pl));
    polylinesRef.current = [];

    // Clear old node markers
    Object.keys(layersRef.current).forEach((k) => {
      if (k.startsWith('node-')) {
        map.removeLayer(layersRef.current[k]);
        delete layersRef.current[k];
      }
    });

    const nodeCoordMap = new Map<string, [number, number]>(
      districtNodes.map((n) => [n.id, [n.lat, n.lng]])
    );

    // 1. Draw Feeder Lines (Polylines)
    districtFeeders.forEach((feeder) => {
      const p1 = nodeCoordMap.get(feeder.from);
      const p2 = nodeCoordMap.get(feeder.to);
      if (!p1 || !p2) return;

      const { loadingPct, flowKw, isCongested, capacityKw } = feeder;
      const color = isCongested ? '#EF4444' : loadingPct > 75 ? '#F59E0B' : '#0284C7';

      const poly = L.polyline([p1, p2], {
        color,
        weight: isCongested ? 4.5 : 3.5,
        opacity: isCongested ? 0.95 : 0.8,
        dashArray: isCongested ? '8, 6' : undefined,
      }).addTo(map);

      poly.bindTooltip(
        `<div class="font-mono text-xs">
          <strong>${feeder.from} ↔ ${feeder.to}</strong><br/>
          Flow: <strong>${Math.abs(flowKw).toFixed(1)} kW</strong> / ${capacityKw} kW<br/>
          Loading: <span style="color:${color};font-weight:bold">${loadingPct.toFixed(1)}%</span>
          ${isCongested ? '<br/><span style="color:#EF4444;font-weight:bold">⚠ THERMAL CONGESTION</span>' : ''}
        </div>`,
        { sticky: true }
      );

      polylinesRef.current.push(poly);
    });

    // 2. Draw District Markers
    districtNodes.forEach((node) => {
      const isSelected = selectedAssetId === node.id || activeInspector?.id === node.id;
      const lmp = telemetry?.nodal_lmps?.[node.id] ?? telemetry?.market_clearing_price ?? 0.085;

      const customIcon = L.divIcon({
        className: 'custom-map-node',
        html: `
          <div style="position:relative; transform: translate(-50%, -50%); cursor:pointer;">
            ${isSelected ? `<div style="position:absolute; inset:-8px; border-radius:9999px; background:${node.color}; opacity:0.3; animation:ping 1.5s cubic-bezier(0,0,0.2,1) infinite;"></div>` : ''}
            <div style="
              width: 32px;
              height: 32px;
              border-radius: 9999px;
              background: #FFFFFF;
              border: 3px solid ${node.color};
              box-shadow: 0 4px 6px -1px rgba(0,0,0,0.2);
              display: flex;
              align-items: center;
              justify-content: center;
              font-weight: bold;
              font-size: 11px;
              color: #0F172A;
              transition: transform 0.2s;
            ">
              <span style="width:8px; height:8px; border-radius:9999px; background:${node.color}"></span>
            </div>
            <div style="
              position: absolute;
              left: 50%;
              transform: translateX(-50%);
              top: 34px;
              background: rgba(15, 23, 42, 0.9);
              color: #FFFFFF;
              padding: 2px 6px;
              border-radius: 4px;
              font-size: 9.5px;
              font-weight: 700;
              white-space: nowrap;
              box-shadow: 0 2px 4px rgba(0,0,0,0.15);
              pointer-events: none;
              font-family: monospace;
            ">
              ${node.id} ($${Number(lmp).toFixed(3)})
            </div>
          </div>
        `,
        iconSize: [32, 32],
        iconAnchor: [0, 0],
      });

      const marker = L.marker([node.lat, node.lng], { icon: customIcon }).addTo(map);

      marker.on('click', () => {
        setActiveInspector(node);
        if (onSelectAsset) onSelectAsset(node.id);
      });

      marker.bindPopup(`
        <div style="font-family:sans-serif; padding:4px;">
          <h4 style="margin:0 0 4px 0; font-size:13px; font-weight:bold; color:#0F172A;">${node.name}</h4>
          <p style="margin:0 0 6px 0; font-size:11px; color:#64748B;">${node.type} &bull; <strong>${node.voltage}</strong></p>
          <div style="font-family:monospace; font-size:11px; background:#F8FAFC; padding:6px; border-radius:6px; border:1px solid #E2E8F0;">
            <div>Nodal LMP: <strong style="color:#059669;">$${Number(lmp).toFixed(3)}/kWh</strong></div>
            <div>Voltage Level: <strong>${node.voltage}</strong></div>
            <div>Operating Mode: <strong>${node.isGen ? 'Generation Feed' : 'Load Bus'}</strong></div>
          </div>
        </div>
      `);

      layersRef.current[`node-${node.id}`] = marker;
    });
  }, [districtNodes, districtFeeders, telemetry, selectedAssetId, activeInspector, onSelectAsset]);

  const handleResetView = () => {
    mapInstanceRef.current?.flyTo(activeCenter, 16, { duration: 1.2 });
  };

  return (
    <div
      className={clsx(
        'relative w-full rounded-xl overflow-hidden border border-slate-200 bg-slate-100 shadow-sm flex flex-col justify-between',
        heightClass,
        className
      )}
    >
      {/* 1. Map Container */}
      <div ref={mapContainerRef} className="absolute inset-0 z-0" />

      {/* Loading Overlay */}
      {districtNodes.length === 0 && (
        <div className="absolute inset-0 z-20 bg-slate-900/60 backdrop-blur-xs flex flex-col items-center justify-center text-white font-mono text-xs gap-2">
          <div className="w-6 h-6 border-2 border-emerald-400 border-t-transparent rounded-full animate-spin" />
          <span>Fetching grid topology from backend...</span>
        </div>
      )}

      {/* 2. Top Floating Controls Bar */}
      <div className="relative z-10 m-3 flex items-center justify-between pointer-events-none">
        {/* District Status Badge */}
        <div className="pointer-events-auto flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white/95 border border-slate-200 shadow-sm backdrop-blur-md">
          <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse" />
          <span className="text-xs font-bold text-slate-900 tracking-tight">
            {topology?.location?.label || 'GreenGrid District'}
          </span>
          <span className="text-[10px] text-slate-500 font-mono pl-1.5 border-l border-slate-200">
            {telemetry?.operating_mode || 'GRID_CONNECTED'}
          </span>
        </div>

        {/* Layer & Navigation Controls */}
        <div className="pointer-events-auto flex items-center gap-1.5 bg-white/95 border border-slate-200 p-1 rounded-lg shadow-sm backdrop-blur-md">
          {/* Dynamic Location Selector */}
          <div className="flex items-center gap-1 pl-1.5 pr-2 border-r border-slate-200 text-slate-700">
            <MapPin className="w-3.5 h-3.5 text-blue-600 shrink-0" />
            <select
              value={selectedLocationKey}
              onChange={(e) => setSelectedLocationKey(e.target.value)}
              className="text-xs font-semibold bg-transparent text-slate-800 outline-none cursor-pointer pr-1"
              title="Switch Microgrid Geographic Location"
            >
              <option value="auto">
                📍 {topology?.location?.label || 'Lahore, PK (Auto)'}
              </option>
              <option value="lahore">📍 Lahore, Pakistan</option>
              <option value="sf">📍 San Francisco, CA</option>
              <option value="london">📍 London, UK</option>
              <option value="newyork">📍 New York, NY</option>
            </select>
          </div>

          <button
            onClick={() => setMapStyle('streets')}
            className={clsx(
              'px-2.5 py-1 rounded text-xs font-semibold transition-all',
              mapStyle === 'streets'
                ? 'bg-blue-600 text-white shadow-xs'
                : 'text-slate-600 hover:text-slate-900'
            )}
            title="Carto Voyager GIS Vector/Raster"
          >
            Carto GIS
          </button>
          <button
            onClick={() => setMapStyle('dark')}
            className={clsx(
              'px-2.5 py-1 rounded text-xs font-semibold transition-all',
              mapStyle === 'dark'
                ? 'bg-blue-600 text-white shadow-xs'
                : 'text-slate-600 hover:text-slate-900'
            )}
            title="Carto Dark Matter SCADA Mode"
          >
            Dark SCADA
          </button>
          <button
            onClick={() => setMapStyle('satellite')}
            className={clsx(
              'px-2.5 py-1 rounded text-xs font-semibold transition-all',
              mapStyle === 'satellite'
                ? 'bg-blue-600 text-white shadow-xs'
                : 'text-slate-600 hover:text-slate-900'
            )}
            title="Satellite Imagery"
          >
            Satellite
          </button>
          <button
            onClick={handleResetView}
            className="p-1 text-slate-500 hover:text-slate-800 hover:bg-slate-100 rounded transition-colors ml-0.5"
            title="Reset to District Center"
          >
            <Maximize2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* 3. Bottom Floating Telemetry & Asset Inspector Bar */}
      <div className="relative z-10 m-3 pointer-events-auto">
        {activeInspector ? (
          <div className="p-3 rounded-lg bg-slate-900/95 border border-slate-700 text-white shadow-xl backdrop-blur-md flex items-center justify-between text-xs animate-in fade-in slide-in-from-bottom-2">
            <div className="flex items-center gap-3">
              <div
                className="w-3.5 h-3.5 rounded-full shrink-0"
                style={{ backgroundColor: activeInspector.color }}
              />
              <div>
                <span className="font-bold text-white text-sm">{activeInspector.name}</span>
                <span className="text-slate-400 ml-2 font-mono text-[11px]">
                  ({activeInspector.type} &bull; {activeInspector.voltage})
                </span>
              </div>
            </div>
            <div className="flex items-center gap-4 font-mono text-[11px]">
              <span>
                LMP:{' '}
                <strong className="text-emerald-400">
                  $
                  {(
                    telemetry?.nodal_lmps?.[activeInspector.id] ??
                    telemetry?.market_clearing_price ??
                    0.085
                  ).toFixed(3)}
                  /kWh
                </strong>
              </span>
              <span>
                Rating: <strong className="text-white">{activeInspector.kw} kW</strong>
              </span>
              <button
                onClick={() => {
                  setActiveInspector(null);
                  if (onSelectAsset) onSelectAsset(null);
                }}
                className="text-slate-400 hover:text-white underline text-[10px]"
              >
                Close
              </button>
            </div>
          </div>
        ) : (
          <div className="p-2.5 rounded-lg bg-slate-900/90 border border-slate-700 text-white shadow-md backdrop-blur-md flex items-center justify-between text-xs font-mono">
            <div className="flex items-center gap-4">
              <span>
                Freq: <strong className="text-emerald-400">{(telemetry?.system_frequency_hz ?? 50.0).toFixed(2)} Hz</strong>
              </span>
              <span>
                Gen: <strong className="text-white">{Math.round(telemetry?.total_generation_kw ?? 0).toLocaleString()} kW</strong>
              </span>
              <span>
                Load: <strong className="text-white">{Math.round(telemetry?.total_load_kw ?? 0).toLocaleString()} kW</strong>
              </span>
              <span>
                Import: <strong className="text-white">{Math.round(telemetry?.grid_import_kw ?? 0).toLocaleString()} kW</strong>
              </span>
            </div>
            <span className="text-[11px] text-slate-300">
              Click any substation or feeder marker to inspect AC nodal metrics
            </span>
          </div>
        )}
      </div>
    </div>
  );
};
