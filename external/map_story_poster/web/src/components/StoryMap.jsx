import React, { useEffect, useRef } from 'react';

export default function StoryMap({ locations }) {
  const mapRef = useRef(null);
  const mapInstanceRef = useRef(null);
  const markersRef = useRef([]);

  // Initialize Map
  useEffect(() => {
    if (!mapRef.current) return;
    if (mapInstanceRef.current) return;

    // Default center (China)
    const map = window.L.map(mapRef.current).setView([35.8617, 104.1954], 4);

    // Add Tile Layer (Esri World Physical for historical feel)
    window.L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Physical_Map/MapServer/tile/{z}/{y}/{x}', {
      attribution: 'Tiles &copy; Esri',
      maxZoom: 8
    }).addTo(map);
    
    // Add labels layer (optional, for clarity)
    window.L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}', {
      attribution: 'Labels &copy; Esri'
    }).addTo(map);

    mapInstanceRef.current = map;

    return () => {
      if (mapInstanceRef.current) {
        mapInstanceRef.current.remove();
        mapInstanceRef.current = null;
      }
    };
  }, []);

  // Update Markers
  useEffect(() => {
    if (!mapInstanceRef.current) return;
    const map = mapInstanceRef.current;

    // Clear old layers
    markersRef.current.forEach(layer => layer.remove());
    markersRef.current = [];

    if (!locations || locations.length === 0) return;

    const latlngs = [];

    locations.forEach((loc) => {
      if (!loc.lat || !loc.lng) return;
      
      const latlng = [loc.lat, loc.lng];
      latlngs.push(latlng);

      // Determine color
      let color = '#3b82f6'; // blue-500
      if (loc.type === 'birth') color = '#22c55e'; // green-500
      if (loc.type === 'death') color = '#ef4444'; // red-500

      // Create Marker
      const marker = window.L.circleMarker(latlng, {
        color: 'white',
        weight: 1,
        fillColor: color,
        fillOpacity: 0.8,
        radius: 8
      }).addTo(map);

      // Popup content
      const popupContent = `
        <div style="min-width: 200px;">
          <h3 style="font-weight: bold; font-size: 1.1em; margin-bottom: 4px;">${loc.name}</h3>
          <div style="font-size: 0.9em; color: #666; margin-bottom: 8px;">
            ${loc.time ? `<span>${loc.time}</span>` : ''}
            ${loc.type === 'birth' ? ' <span style="color:#22c55e">(出生地)</span>' : ''}
            ${loc.type === 'death' ? ' <span style="color:#ef4444">(去世地)</span>' : ''}
          </div>
          <p style="margin: 0; font-size: 0.95em; line-height: 1.5;">${loc.desc || '暂无详细描述'}</p>
          ${loc.quotes && loc.quotes.length ? `
            <div style="margin-top: 8px; border-top: 1px solid #eee; padding-top: 6px;">
              <em style="font-size: 0.85em; color: #555;">"${loc.quotes[0]}"</em>
            </div>
          ` : ''}
        </div>
      `;
      marker.bindPopup(popupContent);
      markersRef.current.push(marker);
    });

    // Draw path
    if (latlngs.length > 1) {
      const polyline = window.L.polyline(latlngs, {
        color: '#6366f1', // indigo-500
        weight: 2,
        opacity: 0.6,
        dashArray: '5, 10'
      }).addTo(map);
      markersRef.current.push(polyline);
      
      // Decorate with arrows if we had leaflet-polyline-decorator, but skip for now
    }

    // Fit bounds
    if (latlngs.length > 0) {
      const bounds = window.L.latLngBounds(latlngs);
      map.fitBounds(bounds, { padding: [50, 50], maxZoom: 6 });
    }

  }, [locations]);

  return <div ref={mapRef} className="w-full h-full rounded-lg shadow-inner bg-gray-100" />;
}
