const GEOCODE_CACHE = new Map();

export async function geocodeCity(name) {
  if (!name) return null;
  // Check memory cache first
  if (GEOCODE_CACHE.has(name)) return GEOCODE_CACHE.get(name);
  
  // Try to use localStorage for persistence across reloads
  const cacheKey = `geocode_${name}`;
  const stored = localStorage.getItem(cacheKey);
  if (stored) {
    try {
      const parsed = JSON.parse(stored);
      GEOCODE_CACHE.set(name, parsed);
      return parsed;
    } catch (e) {
      localStorage.removeItem(cacheKey);
    }
  }

  // Use Nominatim (OpenStreetMap)
  // Note: Nominatim Usage Policy requires a valid User-Agent
  const url = `https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(name)}&limit=1`;
  
  try {
    const resp = await fetch(url, {
      headers: {
        "User-Agent": "MapStory-Trae-App/1.0"
      }
    });
    
    if (!resp.ok) return null;
    
    const data = await resp.json();
    if (data && data.length > 0) {
      const { lat, lon } = data[0];
      const result = { lat: parseFloat(lat), lng: parseFloat(lon) };
      
      // Update caches
      GEOCODE_CACHE.set(name, result);
      localStorage.setItem(cacheKey, JSON.stringify(result));
      
      // Be nice to the API (simple delay if calling in loop)
      await new Promise(resolve => setTimeout(resolve, 1000));
      
      return result;
    }
  } catch (e) {
    console.error(`Geocoding failed for ${name}:`, e);
  }
  return null;
}
